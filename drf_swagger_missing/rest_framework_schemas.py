from collections import OrderedDict

import coreschema
from rest_framework.schemas.generators import SchemaGenerator, insert_into, LinkNode
from rest_framework import serializers
from rest_framework.schemas.inspectors import field_to_schema


class BetterSchemaGenerator(SchemaGenerator):
    prefix = ''
    definitions = OrderedDict()
    check_view_permissions = True

    def __init__(self, title=None, url=None, description=None, patterns=None, urlconf=None,
                 definitions=None, version='', check_view_permissions=True):
        super().__init__(title, url, description, patterns, urlconf)
        if isinstance(definitions, list):
            definitions = OrderedDict([(p.title, p) for p in definitions])
        if definitions:
            self.definitions.update(definitions)
        self.version = version
        self.check_view_permissions = check_view_permissions

    def get_schema(self, request=None, public=False):
        """Add the base path and definitions to the document"""
        schema = super().get_schema(request, public)
        schema._base_path = self.prefix
        schema._definitions.update(self.definitions)
        schema._version = self.version
        return schema

    def get_filter_fields(self, path, method, view):
        """Hack to add extra fields"""
        fields = super().get_filter_fields(path, method, view)
        method_name = getattr(view, 'action', method.lower())
        try:
            fields += view.Meta.fields.get(method_name, [])
        except AttributeError:
            # The view doesn't have Meta, Meta doesn't have .fields
            pass
        return fields

    def old_get_link(self, path, method, view):
        link = super().get_link(path, method, view)
        method = method.lower()
        method_name = getattr(view, 'action', method.lower())
        link._responses = OrderedDict()
        # Add obvious responses based on common action names used in viewsets
        try:
            serializer_name = view.get_serializer().__class__.__name__
            if method_name in ('retrieve', 'update', 'partial_update'):
                response = coreschema.Response(status=200, schema=coreschema.Ref('%s_read' % serializer_name))
            elif method_name == 'list':
                response = coreschema.Response(status=200, schema=coreschema.Array(
                    items=coreschema.Ref('%s_read' % serializer_name)))
            elif method_name == 'create':
                response = coreschema.Response(status=201, schema=coreschema.Ref('%s_write' % serializer_name))
            elif method_name == 'destroy':
                response = coreschema.Response(status=204)
            else:
                response = None
            if response:
                link._responses[response.status] = response
        except AttributeError:
            # not all views have get_serializer
            pass
        # User may want to add responses or overwrite existing
        try:
            # User defined responses come in a list
            for r in view.Meta.responses[method_name]:
                link._responses[r.status] = r
        except (AttributeError, KeyError):
            # The view doesn't have Meta, Meta doesn't have .responses or responses doesn't have this method
            pass

        # User may define what content types the view may produce:
        try:
            # User defined responses come in a list
            link._produces = view.Meta.produces[method_name]
        except (AttributeError, KeyError):
            # The view doesn't have Meta or Meta doesn't have .produces
            link._produces = []
        return link

    def get_links(self, request=None):
        """Almost copy of parent, here I use subpath to create the link and save the base path
        Also I call the new get definitions, which generate object definitions from serializers ued in views"""
        links = LinkNode()

        # Generate (path, method, view) given (path, method, callback).
        paths = []
        view_endpoints = []
        for path, method, callback in self.endpoints:
            view = self.create_view(callback, method, request)
            if getattr(view, 'exclude_from_schema', False):
                continue
            path = self.coerce_path(path, method, view)
            paths.append(path)
            view_endpoints.append((path, method, view))

        # Only generate the path prefix for paths that will be included
        if not paths:
            return None
        self.prefix = self.determine_path_prefix(paths)

        for path, method, view in view_endpoints:
            if self.check_view_permissions and not self.has_view_permissions(path, method, view):
                continue
            subpath = path[len(self.prefix):]
            link = view.schema.get_link(subpath, method, base_url=self.url)
            keys = self.get_keys(subpath, method, view)
            insert_into(links, keys, link)
            obj_def = self.add_object_definitions(method, view)
            if obj_def:
                if obj_def.title not in self.definitions:
                    self.definitions[obj_def.title] = obj_def
        return links

    def add_object_definitions(self, method, view):
        """Create an Object definition from serializer
        It will create a different definitions depending on the method, definition name is
        {serializer class name}_{read|write}
        POST, PUT, PATCH is write
        GET, DELETE, HEAD is read
        write methods will not include read only fields
        read methods will not include write only fields
        Note that for write methods it will also return a read definition because by default this is the definition
        object returned by write methods
        :param str method: GET, POST etc
        :param rest_framework.generics.GenericAPIView view:
        """
        if not hasattr(view, 'get_serializer'):
            return None
        serializer = view.get_serializer()
        if method in ('POST', 'PUT', 'PATCH'):
            write = True
            # also generate a read definition, because it is commonly used as response for write actions
            self.add_object_definitions('GET', view)
            name = '%s_write' % serializer.__class__.__name__
        elif method in ('GET', 'DELETE', 'HEAD'):
            write = False
            name = '%s_read' % serializer.__class__.__name__
        else:
            assert False, 'Can not recognize method %s' % method
        if name in self.definitions:
            return
        fields = []
        for field in serializer.fields.values():
            if isinstance(field, serializers.HiddenField) or write and field.read_only or \
                            not write and field.write_only:
                continue

            # required = bool(field.required)  # field.required is a list
            field = field_to_schema(field)
            fields.append(field)

        self.definitions[name] = coreschema.Object(title=name, properties=fields)
        return self.definitions[name]
