#!/usr/bin/env python

# Copyright 2013 Hewlett-Packard Development Company, L.P.
# Copyright 2011-2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

# A test script to stand in for a zeromq enabled jenkins.  It sends zmq
# events that simulate the jenkins node lifecycle.
#
# Usage:
#   zmq-server.py start HOSTNAME
#   zmq-server.py complete HOSTNAME

import gear
import json
import logging
import select
import socket
import threading
import zmq

class MyGearmanServer(gear.Server):
    def handleStatus(self, request):
        request.connection.conn.send(("build:fake_job\t%s\t0\t0\n" %
                                      self._count).encode('utf8'))
        request.connection.conn.send(("build:fake_job:devstack-precise\t%s\t0\t0\n" %
                                      0).encode('utf8'))
        request.connection.conn.send(b'.\n')

class FakeStatsd(object):
    def __init__(self):
        self.thread = threading.Thread(target=self.run)
        self.thread.daemon = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 8125))
        self.stats = []
        self.thread.start()

    def run(self):
        while True:
            poll = select.poll()
            poll.register(self.sock, select.POLLIN)
            ret = poll.poll()
            for (fd, event) in ret:
                if fd == self.sock.fileno():
                    data = self.sock.recvfrom(1024)
                    if not data:
                        return
                    print data[0]
                    self.stats.append(data[0])

def main():
    logging.basicConfig(level=logging.DEBUG)
    context = zmq.Context()
    zsocket = context.socket(zmq.PUB)
    zsocket.bind("tcp://*:8881")

    geard = MyGearmanServer(statsd_host='localhost', statsd_port=8125,
                            statsd_prefix='zuul.geard')
    geard._count = 0

    statsd = FakeStatsd()

    print('ready')
    while True:
        line = raw_input()
        command, arg = line.split()
        if command == 'queue':
            geard._count = int(arg)
        elif command == 'start':
            topic = 'onStarted'
            data = {"name":"test","url":"job/test/","build":{"full_url":"http://localhost:8080/job/test/1/","number":1,"phase":"STARTED","url":"job/test/1/","node_name":arg}}
            zsocket.send("%s %s" % (topic, json.dumps(data)))
        elif command == 'complete':
            topic = 'onFinalized'
            data = {"name":"test","url":"job/test/","build":{"full_url":"http://localhost:8080/job/test/1/","number":1,"phase":"FINISHED","status":"SUCCESS","url":"job/test/1/","node_name":arg, "parameters":{"BASE_LOG_PATH":"05/60105/3/gate","LOG_PATH":"05/60105/3/gate/gate-tempest-dsvm-postgres-full/bf0f215","OFFLINE_NODE_WHEN_COMPLETE":"1","ZUUL_BRANCH":"master","ZUUL_CHANGE":"60105","ZUUL_CHANGE_IDS":"60105,3","ZUUL_CHANGES":"openstack/cinder:master:refs/changes/05/60105/3","ZUUL_COMMIT":"ccd02fce4148d5ac2b3e1e68532b55eb5c1c356d","ZUUL_PATCHSET":"3","ZUUL_PIPELINE":"gate","ZUUL_PROJECT":"openstack/cinder","ZUUL_REF":"refs/zuul/master/Z6726d84e57a04ec79585b895ace08f7e","ZUUL_URL":"http://zuul.openstack.org/p","ZUUL_UUID":"bf0f21577026492a985ca98a9ea14cc1"}}}
            zsocket.send("%s %s" % (topic, json.dumps(data)))

if __name__ == '__main__':
    main()
