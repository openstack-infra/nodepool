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
import novaclient
import requests.exceptions
import threading
import time
import uuid

from jenkins import JenkinsException
import shade


class Dummy(object):
    IMAGE = 'Image'
    INSTANCE = 'Instance'
    FLAVOR = 'Flavor'

    def __init__(self, kind, **kw):
        self.__kind = kind
        self.__kw = kw
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self):
        args = []
        for k in self.__kw.keys():
            args.append('%s=%s' % (k, getattr(self, k)))
        args = ' '.join(args)
        return '<%s %s %s>' % (self.__kind, id(self), args)

    def delete(self):
        self.manager.delete(self)

    def update(self, data):
        try:
            if self.should_fail:
                raise shade.OpenStackCloudException('This image has '
                                                    'SHOULD_FAIL set to True.')
        except AttributeError:
            pass


def fake_get_one_cloud(cloud_config, cloud_kwargs):
    cloud_kwargs['validate'] = False
    return cloud_config.get_one_cloud(**cloud_kwargs)


class FakeList(object):
    log = logging.getLogger("nodepool.FakeList")

    def __init__(self, l):
        self._list = l

    def list(self):
        self.log.debug("List %s" % repr(self._list))
        return self._list

    def find(self, name):
        for x in self._list:
            if x.name == name:
                return x

    def get(self, image=None):
        if image:
            id = image
        self.log.debug("Get %s in %s" % (id, repr(self._list)))
        for x in self._list:
            if x.id == id:
                return x
        raise novaclient.exceptions.NotFound(404)

    def _finish(self, obj, delay, status):
        time.sleep(delay)
        obj.status = status

    def delete(self, *args, **kw):
        self.log.debug("Delete from %s" % repr(self._list))
        if 'image' in kw:
            self._list.remove(self.get(kw['image']))
        else:
            obj = args[0]
            if hasattr(obj, 'id'):
                self._list.remove(obj)
            else:
                self._list.remove(self.get(obj))
        self.log.debug("Deleted from %s" % repr(self._list))

    def create(self, **kw):
        should_fail = kw.get('SHOULD_FAIL', '').lower() == 'true'
        nics = kw.get('nics', [])
        addresses = None
        # if keyword 'ipv6-uuid' is found in provider config,
        # ipv6 address will be available in public addr dict.
        for nic in nics:
            if 'ipv6-uuid' not in nic['net-id']:
                continue
            addresses = dict(
                public=[dict(version=4, addr='fake'),
                        dict(version=6, addr='fake_v6')],
                private=[dict(version=4, addr='fake')]
            )
            break
        if not addresses:
            addresses = dict(
                public=[dict(version=4, addr='fake')],
                private=[dict(version=4, addr='fake')]
            )
        s = Dummy(Dummy.INSTANCE,
                  id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  adminPass='fake',
                  addresses=addresses,
                  metadata=kw.get('meta', {}),
                  manager=self,
                  should_fail=should_fail)
        self._list.append(s)
        t = threading.Thread(target=self._finish,
                             name='FakeProvider create',
                             args=(s, 0.1, 'ACTIVE'))
        t.start()
        return s

    def create_image(self, server, image_name, metadata):
        # XXX : validate metadata?
        x = self.api.images.create(name=image_name)
        return x.id


class FakeHTTPClient(object):
    def get(self, path):
        if path == '/extensions':
            return None, dict(extensions=dict())


class BadHTTPClient(object):
    '''Always raises a ProxyError'''
    def get(self, path):
        raise requests.exceptions.ProxyError


class FakeClient(object):
    def __init__(self, images, *args, **kwargs):
        self.flavors = FakeList([
            Dummy(Dummy.FLAVOR, id='f1', ram=8192, name='Fake Flavor'),
            Dummy(Dummy.FLAVOR, id='f2', ram=8192, name='Unreal Flavor'),
        ])
        self.images = images
        self.client = FakeHTTPClient()
        self.servers = FakeList([])
        self.servers.api = self


class BadClient(FakeClient):
    def __init__(self, images):
        super(BadClient, self).__init__(images)
        self.client = BadHTTPClient()


class BadOpenstackCloud(object):
    def __init__(self, images=None):
        if images is None:
            images = FakeList([Dummy(Dummy.IMAGE,
                                     id='fake-image-id',
                                     status='READY',
                                     name='Fake Precise',
                                     metadata={})])
        self.nova_client = BadClient(images)


class FakeGlanceClient(object):
    def __init__(self, images, **kwargs):
        self.kwargs = kwargs
        self.images = images


class FakeOpenStackCloud(object):
    def __init__(self, images=None):
        if images is None:
            images = FakeList([Dummy(Dummy.IMAGE,
                                     id='fake-image-id',
                                     status='READY',
                                     name='Fake Precise',
                                     metadata={})])
        self.nova_client = FakeClient(images)
        self._glance_client = FakeGlanceClient(images)

    def create_image(self, **kwargs):
        image = self._glance_client.images.create(**kwargs)
        image.update('fake data')
        return image


class FakeFile(StringIO.StringIO):
    def __init__(self, path):
        StringIO.StringIO.__init__(self)
        self.__path = path

    def close(self):
        print "Wrote to %s:" % self.__path
        print self.getvalue()
        StringIO.StringIO.close(self)


class FakeSFTPClient(object):
    def open(self, path, mode):
        return FakeFile(path)

    def close(self):
        pass


class FakeSSHClient(object):
    def __init__(self):
        self.client = self

    def ssh(self, description, cmd, output=False):
        return True

    def scp(self, src, dest):
        return True

    def open_sftp(self):
        return FakeSFTPClient()


class FakeJenkins(object):
    def __init__(self, user):
        self._nodes = {}
        self.quiet = False
        self.down = False
        if user == 'quiet':
            self.quiet = True
        if user == 'down':
            self.down = True

    def node_exists(self, name):
        return name in self._nodes

    def create_node(self, name, **kw):
        self._nodes[name] = kw

    def delete_node(self, name):
        del self._nodes[name]

    def get_info(self):
        if self.down:
            raise JenkinsException("Jenkins is down")
        d = {u'assignedLabels': [{}],
             u'description': None,
             u'jobs': [{u'color': u'red',
                        u'name': u'test-job',
                        u'url': u'https://jenkins.example.com/job/test-job/'}],
             u'mode': u'NORMAL',
             u'nodeDescription': u'the master Jenkins node',
             u'nodeName': u'',
             u'numExecutors': 1,
             u'overallLoad': {},
             u'primaryView': {u'name': u'Overview',
                              u'url': u'https://jenkins.example.com/'},
             u'quietingDown': self.quiet,
             u'slaveAgentPort': 8090,
             u'unlabeledLoad': {},
             u'useCrumbs': False,
             u'useSecurity': True,
             u'views': [
                 {u'name': u'test-view',
                  u'url': u'https://jenkins.example.com/view/test-view/'}]}
        return d
