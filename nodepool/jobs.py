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
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import uuid
import threading

import gear


class WatchableJob(gear.Job):
    def __init__(self, *args, **kwargs):
        super(WatchableJob, self).__init__(*args, **kwargs)
        self._completion_handlers = []
        self._event = threading.Event()

    def _handleCompletion(self, mode=None):
        self._event.set()
        for handler in self._completion_handlers:
            handler(self)

    def addCompletionHandler(self, handler):
        self._completion_handlers.append(handler)

    def onCompleted(self):
        self._handleCompletion()

    def onFailed(self):
        self._handleCompletion()

    def onDisconnect(self):
        self._handleCompletion()

    def onWorkStatus(self):
        pass

    def waitForCompletion(self, timeout=None):
        return self._event.wait(timeout)


class NodepoolJob(WatchableJob):
    def __init__(self, job_name, job_data_obj, nodepool):
        job_uuid = str(uuid.uuid4().hex)
        job_data = json.dumps(job_data_obj)
        super(NodepoolJob, self).__init__(job_name, job_data, job_uuid)
        self.nodepool = nodepool

    def getDbSession(self):
        return self.nodepool.getDB().getSession()


class NodeAssignmentJob(NodepoolJob):
    log = logging.getLogger("jobs.NodeAssignmentJob")

    def __init__(self, node_id, target_name, data, nodepool):
        self.node_id = node_id
        job_name = 'node_assign:%s' % target_name
        super(NodeAssignmentJob, self).__init__(job_name, data, nodepool)


class NodeRevokeJob(NodepoolJob):
    log = logging.getLogger("jobs.NodeRevokeJob")

    def __init__(self, node_id, manager_name, data, nodepool):
        self.node_id = node_id
        job_name = 'node_revoke:%s' % manager_name
        super(NodeRevokeJob, self).__init__(job_name, data, nodepool)
