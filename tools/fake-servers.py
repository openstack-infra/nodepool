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

import zmq
import json
import logging
import gear

class MyGearmanServer(gear.Server):
    def handleStatus(self, request):
        request.connection.conn.send(("fake_job\t%s\t0\t0\n" %
                                      self._count).encode('utf8'))
        request.connection.conn.send(("fake_job:nodepool-fake\t%s\t0\t0\n" %
                                      0).encode('utf8'))
        request.connection.conn.send(b'.\n')

def main():
    logging.basicConfig(level=logging.DEBUG)
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind("tcp://*:8881")

    geard = MyGearmanServer()
    geard._count = 0

    print('ready')
    while True:
        line = raw_input()
        command, arg = line.split()
        if command == 'queue':
            geard._count = int(arg)
        elif command == 'start':
            topic = 'onStarted'
            data = {"name":"test","url":"job/test/","build":{"full_url":"http://localhost:8080/job/test/1/","number":1,"phase":"STARTED","url":"job/test/1/","node_name":arg}}
            socket.send("%s %s" % (topic, json.dumps(data)))
        elif command == 'complete':
            topic = 'onFinalized'
            data = {"name":"test","url":"job/test/","build":{"full_url":"http://localhost:8080/job/test/1/","number":1,"phase":"FINISHED","status":"SUCCESS","url":"job/test/1/","node_name":arg}}
            socket.send("%s %s" % (topic, json.dumps(data)))

if __name__ == '__main__':
    main()
