import copy
import django.utils
import json
import sys
sys.modules["django.utils"].copycompat = copy
sys.modules["django.utils.copycompat"] = copy
sys.modules["django.utils"].simplejson = json
sys.modules["django.utils.simplejson"] = json
