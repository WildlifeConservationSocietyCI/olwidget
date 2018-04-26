"""
Example to use olwidget for mapping in the django admin site::

    from olwidget import admin
    from myapp import SomeGeoModel

    admin.site.register(SomeGeoModel, admin.GeoModelAdmin)

If you want to use custom OLWidget options to change the look and feel of the
map, just subclass GeoModelAdmin, and define "options", for example::

    class CustomGeoAdmin(admin.GeoModelAdmin):
        options = {
            'layers': ['google.hybrid'],
            'overlayStyle': {
                'fillColor': '#ffff00',
                'strokeWidth': 5,
            },
            'defaultLon': -72,
            'defaultLat': 44,
            'defaultZoom': 4,
        }

    admin.site.register(SomeGeoModel, CustomGeoAdmin)

A complete list of options is in the olwidget documentation.
"""

from django.contrib.admin import ModelAdmin
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.gis.geos import GeometryCollection
from django.contrib.admin.options import csrf_protect_m
from django.contrib.admin import helpers
from django.contrib import messages
from django.utils.encoding import force_text
from django.http import HttpResponseRedirect
from django.utils.translation import ungettext
from django.utils.translation import gettext as _
from django.core.exceptions import PermissionDenied
from django.template.response import SimpleTemplateResponse, TemplateResponse

from olwidget.forms import apply_maps_to_modelform_fields, fix_initial_data, fix_cleaned_data
from olwidget.widgets import InfoMap
from olwidget.utils import DEFAULT_PROJ

__all__ = ('GeoModelAdmin',)

class GeoModelAdmin(ModelAdmin):
    options = None
    map_template = "olwidget/admin_olwidget.html"
    list_map = None
    list_map_options = None
    maps = None
    change_list_template = "admin/olwidget_change_list.html"
    default_field_class = None

    def get_form(self, *args, **kwargs):
        """
        Get a ModelForm with our own `__init__` and `clean` methods.  However,
        we need to allow ModelForm's metaclass_factory to run unimpeded, so
        dynamically override the methods rather than subclassing.
        """
        # Get the vanilla modelform class
        ModelForm = super(GeoModelAdmin, self).get_form(*args, **kwargs)

        # enclose klass.__init__
        orig_init = ModelForm.__init__
        def new_init(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            fix_initial_data(self.initial, self.initial_data_keymap)

        # enclose klass.clean
        orig_clean = ModelForm.clean
        def new_clean(self):
            orig_clean(self)
            fix_cleaned_data(self.cleaned_data, self.initial_data_keymap)
            return self.cleaned_data

        # Override methods
        ModelForm.__init__ = new_init
        ModelForm.clean = new_clean

        # Rearrange fields
        ModelForm.initial_data_keymap = apply_maps_to_modelform_fields(
                ModelForm.base_fields, self.maps, self.options,
                self.map_template,
                default_field_class=self.default_field_class)
        return ModelForm

    def get_changelist_map(self, cl, request=None):
        """
        Display a map in the admin changelist, with info popups
        """
        if self.list_map:
            info = []
            if request:
                qs = cl.get_queryset(request)
            else:
                qs = cl.get_queryset()
            for obj in qs:
                # Transform the fields into one projection.
                geoms = []
                for field in self.list_map:
                    geom = getattr(obj, field)
                    if geom:
                        if callable(geom):
                            geom = geom()
                        geoms.append(geom)
                for geom in geoms:
                    geom.transform(int(DEFAULT_PROJ))

                if geoms:
                    info.append((
                        GeometryCollection(geoms, srid=int(DEFAULT_PROJ)),
                        "<a href='%s'>%s</a>" % (
                            cl.url_for_result(obj),
                            force_text(obj)
                        )
                    ))

            return InfoMap(info, options=self.list_map_options)
        return None

    @csrf_protect_m
    def changelist_view(self, request, extra_context=None):
        #
        # This implementation is all copied from the parent, and only modified
        # for a few lines where marked to add a map to the change list.
        #
        "The 'change list' admin view for this model."
        from django.contrib.admin.views.main import ERROR_FLAG
        opts = self.model._meta
        app_label = opts.app_label
        if not self.has_change_permission(request, None):
            raise PermissionDenied

        list_display = self.get_list_display(request)
        list_display_links = self.get_list_display_links(request, list_display)
        list_filter = self.get_list_filter(request)
        search_fields = self.get_search_fields(request)

        # Check actions to see if any are available on this changelist
        actions = self.get_actions(request)
        if actions:
            # Add the action checkboxes if there are any actions available.
            list_display = ['action_checkbox'] + list(list_display)

        ChangeList = self.get_changelist(request)
        try:
            cl = ChangeList(request, self.model, list_display,
                list_display_links, list_filter, self.date_hierarchy,
                search_fields, self.list_select_related, self.list_per_page,
                self.list_max_show_all, self.list_editable, self)

        except IncorrectLookupParameters:
            # Wacky lookup parameters were given, so redirect to the main
            # changelist page, without parameters, and pass an 'invalid=1'
            # parameter via the query string. If wacky parameters were given
            # and the 'invalid=1' parameter was already in the query string,
            # something is screwed up with the database, so display an error
            # page.
            if ERROR_FLAG in request.GET.keys():
                return SimpleTemplateResponse('admin/invalid_setup.html', {
                    'title': _('Database error'),
                })
            return HttpResponseRedirect(request.path + '?' + ERROR_FLAG + '=1')

        # If the request was POSTed, this might be a bulk action or a bulk
        # edit. Try to look up an action or confirmation first, but if this
        # isn't an action the POST will fall through to the bulk edit check,
        # below.
        action_failed = False
        selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)

        # Actions with no confirmation
        if (actions and request.method == 'POST' and
                'index' in request.POST and '_save' not in request.POST):
            if selected:
                response = self.response_action(request, queryset=cl.get_queryset(request))
                if response:
                    return response
                else:
                    action_failed = True
            else:
                msg = _("Items must be selected in order to perform "
                        "actions on them. No items have been changed.")
                self.message_user(request, msg, messages.WARNING)
                action_failed = True

        # Actions with confirmation
        if (actions and request.method == 'POST' and
                helpers.ACTION_CHECKBOX_NAME in request.POST and
                'index' not in request.POST and '_save' not in request.POST):
            if selected:
                response = self.response_action(request, queryset=cl.get_queryset(request))
                if response:
                    return response
                else:
                    action_failed = True

        # If we're allowing changelist editing, we need to construct a formset
        # for the changelist given all the fields to be edited. Then we'll
        # use the formset to validate/process POSTed data.
        formset = cl.formset = None

        # Handle POSTed bulk-edit data.
        if (request.method == "POST" and cl.list_editable and
                '_save' in request.POST and not action_failed):
            FormSet = self.get_changelist_formset(request)
            formset = cl.formset = FormSet(request.POST, request.FILES, queryset=cl.result_list)
            if formset.is_valid():
                changecount = 0
                for form in formset.forms:
                    if form.has_changed():
                        obj = self.save_form(request, form, change=True)
                        self.save_model(request, obj, form, change=True)
                        self.save_related(request, form, formsets=[], change=True)
                        change_msg = self.construct_change_message(request, form, None)
                        self.log_change(request, obj, change_msg)
                        changecount += 1

                if changecount:
                    if changecount == 1:
                        name = force_text(opts.verbose_name)
                    else:
                        name = force_text(opts.verbose_name_plural)
                    msg = ungettext("%(count)s %(name)s was changed successfully.",
                                    "%(count)s %(name)s were changed successfully.",
                                    changecount) % {'count': changecount,
                                                    'name': name,
                                                    'obj': force_text(obj)}
                    self.message_user(request, msg, messages.SUCCESS)

                return HttpResponseRedirect(request.get_full_path())

        # Handle GET -- construct a formset for display.
        elif cl.list_editable:
            FormSet = self.get_changelist_formset(request)
            formset = cl.formset = FormSet(queryset=cl.result_list)

        # Build the list of media to be used by the formset.
        if formset:
            media = self.media + formset.media
        else:
            media = self.media

        # Build the action form and populate it with available actions.
        if actions:
            action_form = self.action_form(auto_id=None)
            action_form.fields['action'].choices = self.get_action_choices(request)
        else:
            action_form = None

        selection_note_all = ungettext('%(total_count)s selected',
            'All %(total_count)s selected', cl.result_count)

        context = dict(
            self.admin_site.each_context(request),
            module_name=force_text(opts.verbose_name_plural),
            selection_note=_('0 of %(cnt)s selected') % {'cnt': len(cl.result_list)},
            selection_note_all=selection_note_all % {'total_count': cl.result_count},
            title=cl.title,
            is_popup=cl.is_popup,
            to_field=cl.to_field,
            cl=cl,
            media=media,
            has_add_permission=self.has_add_permission(request),
            opts=cl.opts,
            action_form=action_form,
            actions_on_top=self.actions_on_top,
            actions_on_bottom=self.actions_on_bottom,
            actions_selection_counter=self.actions_selection_counter,
            preserved_filters=self.get_preserved_filters(request),
        )
        context.update(extra_context or {})

        # MODIFICATION
        map_ = self.get_changelist_map(cl)
        if map_:
            context['media'] += map_.media
            context['map'] = map_
        # END MODIFICATION

        request.current_app = self.admin_site.name

        return TemplateResponse(request, self.change_list_template or [
            'admin/%s/%s/change_list.html' % (app_label, opts.model_name),
            'admin/%s/change_list.html' % app_label,
            'admin/change_list.html'
        ], context)
