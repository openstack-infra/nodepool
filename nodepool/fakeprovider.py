#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import StringIO
import logging
import threading
import time
import uuid

import shade

import exceptions


class Dummy(object):
    IMAGE = 'Image'
    INSTANCE = 'Instance'
    FLAVOR = 'Flavor'
    LOCATION = 'Server.Location'

    def __init__(self, kind, **kw):
        self.__kind = kind
        self.__kw = kw
        for k, v in kw.items():
            setattr(self, k, v)
        try:
            if self.should_fail:
                raise shade.OpenStackCloudException('This image has '
                                                    'SHOULD_FAIL set to True.')
        except AttributeError:
            pass

    def __repr__(self):
        args = []
        for k in self.__kw.keys():
            args.append('%s=%s' % (k, getattr(self, k)))
        args = ' '.join(args)
        return '<%s %s %s>' % (self.__kind, id(self), args)

    def __getitem__(self, key, default=None):
        return getattr(self, key, default)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)


def fake_get_one_cloud(cloud_config, cloud_kwargs):
    cloud_kwargs['validate'] = False
    return cloud_config.get_one_cloud(**cloud_kwargs)


class FakeOpenStackCloud(object):
    log = logging.getLogger("nodepool.FakeOpenStackCloud")

    def __init__(self, images=None, networks=None):
        self.pause_creates = False
        self._image_list = images
        if self._image_list is None:
            self._image_list = [
                Dummy(
                    Dummy.IMAGE,
                    id='fake-image-id',
                    status='READY',
                    name='Fake Precise',
                    metadata={})
            ]
        if networks is None:
            networks = [dict(id='fake-public-network-uuid',
                             name='fake-public-network-name'),
                        dict(id='fake-private-network-uuid',
                             name='fake-private-network-name'),
                        dict(id='fake-ipv6-network-uuid',
                             name='fake-ipv6-network-name')]
        self.networks = networks
        self._flavor_list = [
            Dummy(Dummy.FLAVOR, id='f1', ram=8192, name='Fake Flavor'),
            Dummy(Dummy.FLAVOR, id='f2', ram=8192, name='Unreal Flavor'),
        ]
        self._server_list = []

    def _get(self, name_or_id, instance_list):
        self.log.debug("Get %s in %s" % (name_or_id, repr(instance_list)))
        for instance in instance_list:
            if instance.name == name_or_id or instance.id == name_or_id:
                return instance
        return None

    def get_network(self, name_or_id, filters=None):
        for net in self.networks:
            if net['id'] == name_or_id or net['name'] == name_or_id:
                return net
        return self.networks[0]

    def _create(
            self, instance_list, instance_type=Dummy.INSTANCE,
            done_status='ACTIVE', **kw):
        should_fail = kw.get('SHOULD_FAIL', '').lower() == 'true'
        nics = kw.get('nics', [])
        addresses = None
        # if keyword 'ipv6-uuid' is found in provider config,
        # ipv6 address will be available in public addr dict.
        for nic in nics:
            if nic['net-id'] != 'fake-ipv6-network-uuid':
                continue
            addresses = dict(
                public=[dict(version=4, addr='fake'),
                        dict(version=6, addr='fake_v6')],
                private=[dict(version=4, addr='fake')]
            )
            public_v6 = 'fake_v6'
            public_v4 = 'fake'
            private_v4 = 'fake'
            interface_ip = 'fake_v6'
            break
        if not addresses:
            addresses = dict(
                public=[dict(version=4, addr='fake')],
                private=[dict(version=4, addr='fake')]
            )
            public_v6 = ''
            public_v4 = 'fake'
            private_v4 = 'fake'
            interface_ip = 'fake'

        s = Dummy(instance_type,
                  id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  adminPass='fake',
                  addresses=addresses,
                  public_v4=public_v4,
                  public_v6=public_v6,
                  private_v4=private_v4,
                  interface_ip=interface_ip,
                  location=Dummy(Dummy.LOCATION, zone=kw.get('az')),
                  metadata=kw.get('meta', {}),
                  manager=self,
                  key_name=kw.get('key_name', None),
                  should_fail=should_fail,
                  event=threading.Event())
        instance_list.append(s)
        t = threading.Thread(target=self._finish,
                             name='FakeProvider create',
                             args=(s, 0.1, done_status))
        t.start()
        return s

    def _delete(self, name_or_id, instance_list):
        self.log.debug("Delete from %s" % (repr(instance_list),))
        instance = None
        for maybe in instance_list:
            if maybe.name == name_or_id or maybe.id == name_or_id:
                instance = maybe
        if instance:
            instance_list.remove(instance)
        self.log.debug("Deleted from %s" % (repr(instance_list),))

    def _finish(self, obj, delay, status):
        self.log.debug("Pause creates %s", self.pause_creates)
        if self.pause_creates:
            self.log.debug("Pausing")
            obj.event.wait()
            self.log.debug("Continuing")
        else:
            time.sleep(delay)
        obj.status = status

    def create_image(self, **kwargs):
        return self._create(
            self._image_list, instance_type=Dummy.IMAGE,
            done_status='READY', **kwargs)

    def get_image(self, name_or_id):
        return self._get(name_or_id, self._image_list)

    def list_images(self):
        return self._image_list

    def delete_image(self, name_or_id):
        if not name_or_id:
            raise Exception('name_or_id is Empty')
        self._delete(name_or_id, self._image_list)

    def create_image_snapshot(self, name, server, **metadata):
        # XXX : validate metadata?
        return self._create(
            self._image_list, instance_type=Dummy.IMAGE,
            name=name, **metadata)

    def list_flavors(self, get_extra=False):
        return self._flavor_list

    def get_openstack_vars(self, server):
        server.public_v4 = 'fake'
        server.public_v6 = 'fake'
        server.private_v4 = 'fake'
        server.interface_ip = 'fake'
        return server

    def create_server(self, **kw):
        return self._create(self._server_list, **kw)

    def get_server(self, name_or_id):
        result = self._get(name_or_id, self._server_list)
        return result

    def wait_for_server(self, server, **kwargs):
        while server.status == 'BUILD':
            time.sleep(0.1)
        return server

    def list_servers(self):
        return self._server_list

    def delete_server(self, name_or_id, delete_ips=True):
        self._delete(name_or_id, self._server_list)


class FakeUploadFailCloud(FakeOpenStackCloud):
    log = logging.getLogger("nodepool.FakeUploadFailCloud")

    def __init__(self, times_to_fail=None):
        super(FakeUploadFailCloud, self).__init__()
        self.times_to_fail = times_to_fail
        self.times_failed = 0

    def create_image(self, **kwargs):
        if self.times_to_fail is None:
            raise exceptions.BuilderError("Test fail image upload.")
        self.times_failed += 1
        if self.times_failed <= self.times_to_fail:
            raise exceptions.BuilderError("Test fail image upload.")
        else:
            return super(FakeUploadFailCloud, self).create_image(**kwargs)


class FakeFile(StringIO.StringIO):
    def __init__(self, path):
        StringIO.StringIO.__init__(self)
        self.__path = path

    def close(self):
        print "Wrote to %s:" % self.__path
        print self.getvalue()
        StringIO.StringIO.close(self)
