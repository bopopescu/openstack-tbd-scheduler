# Copyright (c) 2011 X.commerce, a business unit of eBay Inc.
# Copyright 2011 Eldar Nugaev
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from lxml import etree
import webob

from nova.api.openstack.compute.contrib import floating_ips
from nova import compute
from nova.compute import utils as compute_utils
from nova import context
from nova import db
from nova import exception
from nova import network
from nova.openstack.common import rpc
from nova import test
from nova.tests.api.openstack import fakes
from nova.tests import fake_network
from nova import utils

FAKE_UUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


def network_api_get_fixed_ip(self, context, id):
    if id is None:
        return None
    return {'address': '10.0.0.1', 'id': id, 'instance_uuid': 1}


def network_api_get_floating_ip(self, context, id):
    return {'id': 1, 'address': '10.10.10.10', 'pool': 'nova',
            'fixed_ip_id': None}


def network_api_get_floating_ip_by_address(self, context, address):
    return {'id': 1, 'address': '10.10.10.10', 'pool': 'nova',
            'fixed_ip_id': 10}


def network_api_get_floating_ips_by_project(self, context):
    return [{'id': 1,
             'address': '10.10.10.10',
             'pool': 'nova',
             'fixed_ip_id': 20},
            {'id': 2,
             'pool': 'nova', 'interface': 'eth0',
             'address': '10.10.10.11',
             'fixed_ip_id': None}]


def compute_api_get(self, context, instance_id):
    return dict(uuid=FAKE_UUID, id=instance_id, instance_type_id=1, host='bob')


def network_api_allocate(self, context):
    return '10.10.10.10'


def network_api_release(self, context, address):
    pass


def compute_api_associate(self, context, instance_id, address):
    pass


def network_api_associate(self, context, floating_address, fixed_address):
    pass


def network_api_disassociate(self, context, instance, floating_address):
    pass


def fake_instance_get(context, instance_id):
        return {
        "id": 1,
        "uuid": utils.gen_uuid(),
        "name": 'fake',
        "user_id": 'fakeuser',
        "project_id": '123'}


def stub_nw_info(stubs):
    def get_nw_info_for_instance(instance):
        return fake_network.fake_get_instance_nw_info(stubs, spectacular=True)
    return get_nw_info_for_instance


def get_instance_by_floating_ip_addr(self, context, address):
    return None


class FloatingIpTest(test.TestCase):
    floating_ip = "10.10.10.10"
    floating_ip_2 = "10.10.10.11"

    def _create_floating_ips(self, floating_ips=None):
        """Create a floating ip object."""
        if floating_ips is None:
            floating_ips = [self.floating_ip]
        elif not isinstance(floating_ips, (list, tuple)):
            floating_ips = [floating_ips]

        def make_ip_dict(ip):
            """Shortcut for creating floating ip dict."""
            return

        dict_ = {'pool': 'nova', 'host': 'fake_host'}
        return db.floating_ip_bulk_create(
            self.context, [dict(address=ip, **dict_) for ip in floating_ips],
        )

    def _delete_floating_ip(self):
        db.floating_ip_destroy(self.context, self.floating_ip)

    def setUp(self):
        super(FloatingIpTest, self).setUp()
        self.stubs.Set(network.api.API, "get_fixed_ip",
                       network_api_get_fixed_ip)
        self.stubs.Set(compute.api.API, "get",
                       compute_api_get)
        self.stubs.Set(network.api.API, "get_floating_ip",
                       network_api_get_floating_ip)
        self.stubs.Set(network.api.API, "get_floating_ip_by_address",
                       network_api_get_floating_ip_by_address)
        self.stubs.Set(network.api.API, "get_floating_ips_by_project",
                       network_api_get_floating_ips_by_project)
        self.stubs.Set(network.api.API, "release_floating_ip",
                       network_api_release)
        self.stubs.Set(network.api.API, "disassociate_floating_ip",
                       network_api_disassociate)
        self.stubs.Set(network.api.API, "get_instance_id_by_floating_address",
                       get_instance_by_floating_ip_addr)
        self.stubs.Set(compute_utils, "get_nw_info_for_instance",
                       stub_nw_info(self.stubs))

        fake_network.stub_out_nw_api_get_instance_nw_info(self.stubs,
                                                          spectacular=True)
        self.stubs.Set(db, 'instance_get',
                       fake_instance_get)

        self.context = context.get_admin_context()
        self._create_floating_ips()

        self.controller = floating_ips.FloatingIPController()
        self.manager = floating_ips.FloatingIPActionController()

    def tearDown(self):
        self._delete_floating_ip()
        super(FloatingIpTest, self).tearDown()

    def test_translate_floating_ip_view(self):
        floating_ip_address = self.floating_ip
        floating_ip = db.floating_ip_get_by_address(self.context,
                                                    floating_ip_address)
        floating_ip['fixed_ip'] = None
        floating_ip['instance'] = None
        view = floating_ips._translate_floating_ip_view(floating_ip)
        self.assertTrue('floating_ip' in view)
        self.assertTrue(view['floating_ip']['id'])
        self.assertEqual(view['floating_ip']['ip'], self.floating_ip)
        self.assertEqual(view['floating_ip']['fixed_ip'], None)
        self.assertEqual(view['floating_ip']['instance_id'], None)

    def test_translate_floating_ip_view_dict(self):
        floating_ip = {'id': 0, 'address': '10.0.0.10', 'pool': 'nova',
                       'fixed_ip': None}
        view = floating_ips._translate_floating_ip_view(floating_ip)
        self.assertTrue('floating_ip' in view)

    def test_floating_ips_list(self):
        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips')
        res_dict = self.controller.index(req)

        response = {'floating_ips': [{'instance_id': FAKE_UUID,
                                      'ip': '10.10.10.10',
                                      'pool': 'nova',
                                      'fixed_ip': '10.0.0.1',
                                      'id': 1},
                                     {'instance_id': None,
                                      'ip': '10.10.10.11',
                                      'pool': 'nova',
                                      'fixed_ip': None,
                                      'id': 2}]}
        self.assertEqual(res_dict, response)

    def test_floating_ip_release_nonexisting(self):
        def fake_get_floating_ip(*args, **kwargs):
            raise exception.FloatingIpNotFound(id=id)

        self.stubs.Set(network.api.API, "get_floating_ip",
                       fake_get_floating_ip)

        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips/9876')
        req.method = 'DELETE'
        res = req.get_response(fakes.wsgi_app())
        self.assertEqual(res.status_int, 404)
        expected_msg = ('{"itemNotFound": {"message": "Floating ip not found '
                                         'for id 9876", "code": 404}}')
        self.assertEqual(res.body, expected_msg)

    def test_floating_ip_show(self):
        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips/1')
        res_dict = self.controller.show(req, 1)

        self.assertEqual(res_dict['floating_ip']['id'], 1)
        self.assertEqual(res_dict['floating_ip']['ip'], '10.10.10.10')
        self.assertEqual(res_dict['floating_ip']['instance_id'], None)

    def test_floating_ip_show_not_found(self):
        def fake_get_floating_ip(*args, **kwargs):
            raise exception.FloatingIpNotFound()

        self.stubs.Set(network.api.API, "get_floating_ip",
                       fake_get_floating_ip)

        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips/9876')

        res = req.get_response(fakes.wsgi_app())
        self.assertEqual(res.status_int, 404)
        expected_msg = ('{"itemNotFound": {"message": "Floating ip not found '
                        'for id 9876", "code": 404}}')
        self.assertEqual(res.body, expected_msg)

    def test_show_associated_floating_ip(self):
        def get_floating_ip(self, context, id):
            return {'id': 1, 'address': '10.10.10.10', 'pool': 'nova',
                    'fixed_ip_id': 11}

        def get_fixed_ip(self, context, id):
            return {'address': '10.0.0.1', 'instance_uuid': 1}

        self.stubs.Set(network.api.API, "get_floating_ip", get_floating_ip)
        self.stubs.Set(network.api.API, "get_fixed_ip", get_fixed_ip)

        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips/1')
        res_dict = self.controller.show(req, 1)

        self.assertEqual(res_dict['floating_ip']['id'], 1)
        self.assertEqual(res_dict['floating_ip']['ip'], '10.10.10.10')
        self.assertEqual(res_dict['floating_ip']['instance_id'], FAKE_UUID)

    def test_recreation_of_floating_ip(self):
        self._delete_floating_ip()
        self._create_floating_ips()

    def test_floating_ip_in_bulk_creation(self):
        self._delete_floating_ip()

        self._create_floating_ips([self.floating_ip, self.floating_ip_2])
        all_ips = db.floating_ip_get_all(self.context)
        ip_list = [ip['address'] for ip in all_ips]
        self.assertIn(self.floating_ip, ip_list)
        self.assertIn(self.floating_ip_2, ip_list)

    def test_fail_floating_ip_in_bulk_creation(self):
        self.assertRaises(exception.FloatingIpExists,
                          self._create_floating_ips,
                          [self.floating_ip, self.floating_ip_2])
        all_ips = db.floating_ip_get_all(self.context)
        ip_list = [ip['address'] for ip in all_ips]
        self.assertIn(self.floating_ip, ip_list)
        self.assertNotIn(self.floating_ip_2, ip_list)

# test floating ip allocate/release(deallocate)
    def test_floating_ip_allocate_no_free_ips(self):
        def fake_call(*args, **kwargs):
            raise exception.NoMoreFloatingIps()

        self.stubs.Set(rpc, "call", fake_call)

        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips')
        self.assertRaises(exception.NoMoreFloatingIps,
                          self.controller.create,
                          req)

    def test_floating_ip_allocate(self):
        def fake1(*args, **kwargs):
            pass

        def fake2(*args, **kwargs):
            return {'id': 1, 'address': '10.10.10.10', 'pool': 'nova'}

        self.stubs.Set(network.api.API, "allocate_floating_ip",
                       fake1)
        self.stubs.Set(network.api.API, "get_floating_ip_by_address",
                       fake2)

        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips')
        res_dict = self.controller.create(req)

        ip = res_dict['floating_ip']

        expected = {
            "id": 1,
            "instance_id": None,
            "ip": "10.10.10.10",
            "fixed_ip": None,
            "pool": 'nova'}
        self.assertEqual(ip, expected)

    def test_floating_ip_release(self):
        req = fakes.HTTPRequest.blank('/v2/fake/os-floating-ips/1')
        self.controller.delete(req, 1)

# test floating ip add/remove -> associate/disassociate

    def test_floating_ip_associate(self):
        body = dict(addFloatingIp=dict(address=self.floating_ip))

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        rsp = self.manager._add_floating_ip(req, 'test_inst', body)
        self.assertTrue(rsp.status_int == 202)

    def test_floating_ip_disassociate(self):
        def get_instance_by_floating_ip_addr(self, context, address):
            if address == '10.10.10.10':
                return 'test_inst'

        self.stubs.Set(network.api.API, "get_instance_id_by_floating_address",
                       get_instance_by_floating_ip_addr)

        body = dict(removeFloatingIp=dict(address='10.10.10.10'))

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        rsp = self.manager._remove_floating_ip(req, 'test_inst', body)
        self.assertTrue(rsp.status_int == 202)

    def test_floating_ip_disassociate_missing(self):
        body = dict(removeFloatingIp=dict(address='10.10.10.10'))

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        rsp = self.manager._remove_floating_ip(req, 'test_inst', body)
        self.assertTrue(rsp.status_int == 404)

    def test_floating_ip_associate_non_existent_ip(self):
        def fake_network_api_associate(self, context, instance,
                                             floating_address=None,
                                             fixed_address=None):
            floating_ips = ["10.10.10.10", "10.10.10.11"]
            if floating_address not in floating_ips:
                    raise exception.FloatingIpNotFoundForAddress()

            self.stubs.Set(network.api.API, "associate_floating_ip",
                                             fake_network_api_associate)

            body = dict(addFloatingIp=dict(address='1.1.1.1'))
            req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
            self.assertRaises(webob.exc.HTTPNotFound,
                              self.manager._add_floating_ip,
                              req, 'test_inst', body)

    def test_floating_ip_disassociate_non_existent_ip(self):
        def network_api_get_floating_ip_by_address(self, context,
                                                         floating_address):
            floating_ips = ["10.10.10.10", "10.10.10.11"]
            if floating_address not in floating_ips:
                    raise exception.FloatingIpNotFoundForAddress()

        self.stubs.Set(network.api.API, "get_floating_ip_by_address",
                                        network_api_get_floating_ip_by_address)

        body = dict(removeFloatingIp=dict(address='1.1.1.1'))
        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        self.assertRaises(webob.exc.HTTPNotFound,
                          self.manager._remove_floating_ip,
                          req, 'test_inst', body)

    def test_floating_ip_disassociate_wrong_instance_uuid(self):
        def get_instance_by_floating_ip_addr(self, context, address):
            if address == '10.10.10.10':
                return 'test_inst'

        self.stubs.Set(network.api.API, "get_instance_id_by_floating_address",
                       get_instance_by_floating_ip_addr)

        wrong_uuid = 'aaaaaaaa-ffff-ffff-ffff-aaaaaaaaaaaa'
        body = dict(removeFloatingIp=dict(address='10.10.10.10'))

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        rsp = self.manager._remove_floating_ip(req, wrong_uuid, body)
        self.assertTrue(rsp.status_int == 404)

    def test_floating_ip_disassociate_wrong_instance_id(self):
        def get_instance_by_floating_ip_addr(self, context, address):
            if address == '10.10.10.10':
                return 'wrong_inst'

        self.stubs.Set(network.api.API, "get_instance_id_by_floating_address",
                       get_instance_by_floating_ip_addr)

        body = dict(removeFloatingIp=dict(address='10.10.10.10'))

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        rsp = self.manager._remove_floating_ip(req, 'test_inst', body)
        self.assertTrue(rsp.status_int == 404)

# these are a few bad param tests

    def test_bad_address_param_in_remove_floating_ip(self):
        body = dict(removeFloatingIp=dict(badparam='11.0.0.1'))

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._remove_floating_ip, req, 'test_inst',
                          body)

    def test_missing_dict_param_in_remove_floating_ip(self):
        body = dict(removeFloatingIp='11.0.0.1')

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._remove_floating_ip, req, 'test_inst',
                          body)

    def test_missing_dict_param_in_add_floating_ip(self):
        body = dict(addFloatingIp='11.0.0.1')

        req = fakes.HTTPRequest.blank('/v2/fake/servers/test_inst/action')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.manager._add_floating_ip, req, 'test_inst',
                          body)


class FloatingIpSerializerTest(test.TestCase):
    def test_default_serializer(self):
        serializer = floating_ips.FloatingIPTemplate()
        text = serializer.serialize(dict(
                floating_ip=dict(
                    instance_id=1,
                    ip='10.10.10.10',
                    fixed_ip='10.0.0.1',
                    id=1)))

        tree = etree.fromstring(text)

        self.assertEqual('floating_ip', tree.tag)
        self.assertEqual('1', tree.get('instance_id'))
        self.assertEqual('10.10.10.10', tree.get('ip'))
        self.assertEqual('10.0.0.1', tree.get('fixed_ip'))
        self.assertEqual('1', tree.get('id'))

    def test_index_serializer(self):
        serializer = floating_ips.FloatingIPsTemplate()
        text = serializer.serialize(dict(
                floating_ips=[
                    dict(instance_id=1,
                         ip='10.10.10.10',
                         fixed_ip='10.0.0.1',
                         id=1),
                    dict(instance_id=None,
                         ip='10.10.10.11',
                         fixed_ip=None,
                         id=2)]))

        tree = etree.fromstring(text)

        self.assertEqual('floating_ips', tree.tag)
        self.assertEqual(2, len(tree))
        self.assertEqual('floating_ip', tree[0].tag)
        self.assertEqual('floating_ip', tree[1].tag)
        self.assertEqual('1', tree[0].get('instance_id'))
        self.assertEqual('None', tree[1].get('instance_id'))
        self.assertEqual('10.10.10.10', tree[0].get('ip'))
        self.assertEqual('10.10.10.11', tree[1].get('ip'))
        self.assertEqual('10.0.0.1', tree[0].get('fixed_ip'))
        self.assertEqual('None', tree[1].get('fixed_ip'))
        self.assertEqual('1', tree[0].get('id'))
        self.assertEqual('2', tree[1].get('id'))