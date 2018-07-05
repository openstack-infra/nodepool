#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
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

import threading
import logging
import queue
import time

from nodepool import stats


class ManagerStoppedException(Exception):
    pass


class TaskManager(object):
    log = logging.getLogger("nodepool.TaskManager")

    def __init__(self, client, name, rate):
        super(TaskManager, self).__init__()
        self.daemon = True
        self.queue = queue.Queue()
        self._running = True
        self.name = name
        self.rate = float(rate)
        self._client = None
        self.statsd = stats.get_client()
        self._thread = threading.Thread(name=name, target=self.run)
        self._thread.daemon = True

    def setClient(self, client):
        self._client = client

    def start(self):
        self._thread.start()

    def stop(self):
        self._running = False
        self.queue.put(None)

    def join(self):
        self._thread.join()

    def run(self):
        last_ts = 0
        try:
            while True:
                task = self.queue.get()
                if not task:
                    if not self._running:
                        break
                    continue
                while True:
                    delta = time.time() - last_ts
                    if delta >= self.rate:
                        break
                    time.sleep(self.rate - delta)
                self.log.debug("Manager %s running task %s (queue: %s)" %
                               (self.name, type(task).__name__,
                                self.queue.qsize()))
                start = time.time()
                self.runTask(task)
                last_ts = time.time()
                dt = last_ts - start
                self.log.debug("Manager %s ran task %s in %ss" %
                               (self.name, type(task).__name__, dt))
                if self.statsd:
                    # nodepool.task.PROVIDER.subkey
                    subkey = type(task).__name__
                    key = 'nodepool.task.%s.%s' % (self.name, subkey)
                    self.statsd.timing(key, int(dt * 1000))
                    self.statsd.incr(key)

                self.queue.task_done()
        except Exception:
            self.log.exception("Task manager died.")
            raise

    def submitTask(self, task):
        if not self._running:
            raise ManagerStoppedException(
                "Manager %s is no longer running" % self.name)
        self.queue.put(task)
        return task.wait()

    def runTask(self, task):
        task.run(self._client)
