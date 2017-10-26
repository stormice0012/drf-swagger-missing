from django.db import models


class Pizza(models.Model):
    name = models.TextField()
    paid = models.BooleanField()


class Topping(models.Model):
    name = models.TextField()
