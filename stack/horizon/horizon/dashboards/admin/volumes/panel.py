from django.utils.translation import ugettext_lazy as _

import horizon

from horizon.dashboards.admin import dashboard


class Volumes(horizon.Panel):
    name = _("Volumes")
    slug = "volumes"
    permissions = ('openstack.services.volume',)


dashboard.Admin.register(Volumes)
