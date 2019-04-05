# Copyright (C) 2011-2014 OpenStack Foundation
# Copyright (C) 2017 Red Hat
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

import abc
import collections
import inspect
import importlib
import logging
import math
import os
import voluptuous as v

from nodepool import zk
from nodepool import exceptions


class Drivers:
    """The Drivers plugin interface"""

    log = logging.getLogger("nodepool.driver.Drivers")
    drivers = {}
    drivers_paths = None

    @staticmethod
    def _load_class(driver_name, path, parent_class):
        """Return a driver class that implements the parent_class"""
        spec = importlib.util.spec_from_file_location(driver_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        obj = inspect.getmembers(
            module,
            lambda x: inspect.isclass(x) and issubclass(x, parent_class) and
            x.__module__ == driver_name)
        error = None
        if len(obj) > 1:
            error = "multiple %s implementation" % parent_class
        if not obj:
            error = "no %s implementation found" % parent_class
        if error:
            Drivers.log.error("%s: %s", path, error)
            return False
        return obj[0][1]

    @staticmethod
    def load(drivers_paths=[]):
        """Load drivers"""
        if drivers_paths == Drivers.drivers_paths:
            # Already loaded
            return
        Drivers.drivers.clear()
        for drivers_path in drivers_paths + [os.path.dirname(__file__)]:
            drivers = os.listdir(drivers_path)
            for driver in drivers:
                driver_path = os.path.join(drivers_path, driver)
                if driver in Drivers.drivers:
                    Drivers.log.warning("%s: duplicate driver", driver_path)
                    continue
                if not os.path.isdir(driver_path) or \
                   "__init__.py" not in os.listdir(driver_path):
                    continue
                driver_obj = Drivers._load_class(
                    driver, os.path.join(driver_path, "__init__.py"),
                    Driver)
                if not driver_obj:
                    Drivers.log.error(
                        "%s: skipping incorrect driver from __init__.py",
                        driver_path)
                    continue
                Drivers.drivers[driver] = driver_obj()

        Drivers.drivers_paths = drivers_paths

    @staticmethod
    def get(name):
        if not Drivers.drivers:
            Drivers.load()
        try:
            return Drivers.drivers[name]
        except KeyError:
            raise RuntimeError("%s: unknown driver" % name)


class Driver(object, metaclass=abc.ABCMeta):
    """The Driver interface

    This is the main entrypoint for a Driver.  A single instance of
    this will be created for each driver in the system and will
    persist for the lifetime of the process.

    The class or instance attribute **name** must be provided as a string.

    """

    def reset(self):
        '''
        Called before loading configuration to reset any global state
        '''
        pass

    @abc.abstractmethod
    def getProviderConfig(self, provider):
        """Return a ProviderConfig instance

        :arg dict provider: The parsed provider configuration
        """
        pass

    @abc.abstractmethod
    def getProvider(self, provider_config):
        """Return a Provider instance

        :arg dict provider_config: A ProviderConfig instance
        """
        pass


class ProviderNotifications(object):
    """
    Notification interface for :class:`.Provider` objects.

    This groups all notification messages bound for the Provider. The
    Provider class inherits from this by default. A Provider overrides the
    methods here if they want to handle the notification.
    """

    def nodeDeletedNotification(self, node):
        """
        Called after the ZooKeeper object for a node is deleted.

        :param Node node: Object describing the node just deleted.
        """
        pass


class Provider(ProviderNotifications, metaclass=abc.ABCMeta):
    """The Provider interface

    Drivers implement this interface to supply Providers.  Each
    "provider" in the nodepool configuration corresponds to an
    instance of a class which implements this interface.

    If the configuration is changed, old provider instances will be
    stopped and new ones created as necessary.

    The class or instance attribute **name** must be provided as a string.

    """
    @abc.abstractmethod
    def start(self, zk_conn):
        """Start this provider

        :param ZooKeeper zk_conn: A ZooKeeper connection object.

        This is called after each configuration change to allow the driver
        to perform initialization tasks and start background threads. The
        ZooKeeper connection object is provided if the Provider needs to
        interact with it.

        """
        pass

    @abc.abstractmethod
    def stop(self):
        """Stop this provider

        Before shutdown or reconfiguration, this is called to signal
        to the driver that it will no longer be used.  It should not
        begin any new tasks, but may allow currently running tasks to
        continue.

        """
        pass

    @abc.abstractmethod
    def join(self):
        """Wait for provider to finish

        On shutdown, this is called after
        :py:meth:`~nodepool.driver.Provider.stop` and should return
        when the provider has completed all tasks.  This may not be
        called on reconfiguration (so drivers should not rely on this
        always being called after stop).

        """
        pass

    @abc.abstractmethod
    def labelReady(self, name):
        """Determine if a label is ready in this provider

        If the pre-requisites for this label are ready, return true.
        For example, if the label requires an image that is not
        present, this should return False.  This method should not
        examine inventory or quota.  In other words, it should return
        True if a request for the label would be expected to succeed
        with no resource contention, but False if is not possible to
        satisfy a request for the label.

        :param str name: The name of the label

        :returns: True if the label is ready in this provider, False
           otherwise.
        """
        pass

    @abc.abstractmethod
    def cleanupNode(self, node_id):
        """Cleanup a node after use

        The driver may delete the node or return it to the pool.  This
        may be called after the node was used, or as part of cleanup
        from an aborted launch attempt.

        :param str node_id: The id of the node
        """
        pass

    @abc.abstractmethod
    def waitForNodeCleanup(self, node_id):
        """Wait for a node to be cleaned up

        When called, this will be called after
        :py:meth:`~nodepool.driver.Provider.cleanupNode`.

        This method should return after the node has been deleted or
        returned to the pool.

        :param str node_id: The id of the node
        """
        pass

    @abc.abstractmethod
    def cleanupLeakedResources(self):
        """Clean up any leaked resources

        This is called periodically to give the provider a chance to
        clean up any resources which make have leaked.
        """
        pass

    @abc.abstractmethod
    def getRequestHandler(self, poolworker, request):
        """Return a NodeRequestHandler for the supplied request
        """
        pass

    @abc.abstractmethod
    def listNodes(self):
        # TODO: This is used by the launcher to find leaked instances
        # to delete (see _cleanupLeakedInstances).  It assumes server
        # metadata.  Instead, this should be folded into
        # cleanupLeakedResources so that drivers can figure out how to
        # determine their own leaked instances.
        pass


class LabelRecorder(object):
    def __init__(self):
        self.data = []

    def add(self, label, node_id):
        self.data.append({'label': label, 'node_id': node_id})

    def labels(self):
        '''
        Return labels in the order they were added.
        '''
        labels = []
        for d in self.data:
            labels.append(d['label'])
        return labels

    def pop(self, label):
        '''
        Return the node ID for the (first found) requested label and remove it.
        '''
        for d in self.data:
            if d['label'] == label:
                node_id = d['node_id']
                break
        if not node_id:
            return None
        self.data.remove({'label': label, 'node_id': node_id})
        return node_id

    def removeNode(self, id):
        '''
        Remove the node with the specified ID.
        '''
        for d in self.data:
            if d['node_id'] == id:
                self.data.remove(d)
                return


class NodeRequestHandlerNotifications(object):
    """
    Notification interface for :class:`.NodeRequestHandler` objects.

    This groups all notification messages bound for the NodeRequestHandler.
    The NodeRequestHandler class inherits from this by default. A request
    handler overrides the methods here if they want to handle the notification.
    """

    def nodeReusedNotification(self, node):
        '''
        Handler may implement this to be notified when a node is re-used.
        The OpenStack handler uses this to set the choozen_az.
        '''
        pass


class NodeRequestHandler(NodeRequestHandlerNotifications,
                         metaclass=abc.ABCMeta):
    '''
    Class to process a single nodeset request.

    The PoolWorker thread will instantiate a class of this type for each
    node request that it pulls from ZooKeeper.

    Subclasses are required to implement the launch method.
    '''

    def __init__(self, pw, request):
        '''
        :param PoolWorker pw: The parent PoolWorker object.
        :param NodeRequest request: The request to handle.
        '''
        self.pw = pw
        self.request = request
        self.nodeset = []
        self.done = False
        self.paused = False
        self.launcher_id = self.pw.launcher_id

        self._satisfied_types = LabelRecorder()
        self._failed_nodes = []
        self._ready_nodes = []

    def _setFromPoolWorker(self):
        '''
        Set values that we pull from the parent PoolWorker.

        We don't do this in __init__ because this class is re-entrant and we
        want the updated values.
        '''
        self.provider = self.pw.getProviderConfig()
        self.pool = self.pw.getPoolConfig()
        self.zk = self.pw.getZK()
        self.manager = self.pw.getProviderManager()

    @property
    def failed_nodes(self):
        return self._failed_nodes

    @property
    def ready_nodes(self):
        return self._ready_nodes

    def _invalidNodeTypes(self):
        '''
        Return any node types that are invalid for this provider.

        :returns: A list of node type names that are invalid, or an empty
            list if all are valid.
        '''
        invalid = []
        valid = self.provider.getSupportedLabels(self.pool.name)
        for ntype in self.request.node_types:
            if ntype not in valid:
                invalid.append(ntype)
        return invalid

    def _waitForNodeSet(self):
        '''
        Fill node set for the request.

        Obtain nodes for the request, pausing all new request handling for
        this provider until the node set can be filled.

        note:: This code is a bit racey in its calculation of the number of
            nodes in use for quota purposes. It is possible for multiple
            launchers to be doing this calculation at the same time. Since we
            currently have no locking mechanism around the "in use"
            calculation, if we are at the edge of the quota, one of the
            launchers could attempt to launch a new node after the other
            launcher has already started doing so. This would cause an
            expected failure from the underlying library, which is ok for now.
        '''
        # Since this code can be called more than once for the same request,
        # we need to calculate the difference between our current node set
        # and what was requested. We cannot use set operations here since a
        # node type can appear more than once in the requested types.
        saved_types = collections.Counter(self._satisfied_types.labels())
        requested_types = collections.Counter(self.request.node_types)
        diff = requested_types - saved_types
        needed_types = list(diff.elements())

        if self.request.reuse:
            ready_nodes = self.zk.getReadyNodesOfTypes(needed_types)
        else:
            ready_nodes = []

        for ntype in needed_types:
            # First try to grab from the list of already available nodes.
            got_a_node = False
            if self.request.reuse and ntype in ready_nodes:
                for node in ready_nodes[ntype]:
                    # Only interested in nodes from this provider and pool
                    if node.provider != self.provider.name:
                        continue
                    if node.pool != self.pool.name:
                        continue
                    # Check this driver reuse requirements
                    if not self.checkReusableNode(node):
                        continue
                    try:
                        self.zk.lockNode(node, blocking=False)
                    except exceptions.ZKLockException:
                        # It's already locked so skip it.
                        continue
                    else:
                        # Add an extra safety check that the node is still
                        # ready.
                        if node.state != zk.READY:
                            self.zk.unlockNode(node)
                            continue
                        if self.paused:
                            self.log.debug("Unpaused request %s", self.request)
                            self.paused = False

                        self.log.debug(
                            "Locked existing node %s for request %s",
                            node.id, self.request.id)
                        got_a_node = True
                        node.allocated_to = self.request.id
                        self.zk.storeNode(node)
                        self.nodeset.append(node)
                        self._satisfied_types.add(ntype, node.id)
                        # Notify driver handler about node re-use
                        self.nodeReusedNotification(node)
                        break

            # Could not grab an existing node, so launch a new one.
            if not got_a_node:
                # If we calculate that we're at capacity, pause until nodes
                # are released by Zuul and removed by the DeletedNodeWorker.
                if not self.hasRemainingQuota(ntype):

                    if self.request.requestor == "NodePool:min-ready":
                        # The point of the min-ready nodes is to have nodes on
                        # standby for future requests. When at capacity, it
                        # doesn't make sense to wait for and use resources to
                        # speculatively create a node. Decline this so someone
                        # else with capacity can take it.
                        self.log.debug(
                            "Declining node request %s because provider cannot"
                            " satisfy min-ready", self.request.id)
                        self.decline_request()
                        self._declinedHandlerCleanup()
                        return

                    self.log.info(
                        "Not enough quota remaining to satisfy request %s",
                        self.request.id)
                    if not self.paused:
                        self.log.debug(
                            "Pausing request handling to satisfy request %s",
                            self.request.id)
                    self.paused = True
                    self.zk.deleteOldestUnusedNode(self.provider.name,
                                                   self.pool.name)
                    return

                if self.paused:
                    self.log.debug("Unpaused request %s", self.request)
                    self.paused = False

                node = zk.Node()
                node.state = zk.INIT
                node.type = ntype
                node.provider = self.provider.name
                node.pool = self.pool.name
                node.launcher = self.launcher_id
                node.allocated_to = self.request.id

                # This sets static data defined in the config file in the
                # ZooKeeper Node object.
                node.attributes = self.pool.node_attributes

                self.setNodeMetadata(node)

                # Note: It should be safe (i.e., no race) to lock the node
                # *after* it is stored since nodes in INIT state are not
                # locked anywhere.
                self.zk.storeNode(node)
                self.zk.lockNode(node, blocking=False)
                self.log.debug("Locked building node %s for request %s",
                               node.id, self.request.id)

                # Set state AFTER lock so that it isn't accidentally cleaned
                # up (unlocked BUILDING nodes will be deleted).
                node.state = zk.BUILDING
                self.zk.storeNode(node)

                self.nodeset.append(node)
                self._satisfied_types.add(ntype, node.id)
                self.launch(node)

    def _runHandler(self):
        '''
        Main body for the node request handling.
        '''
        self._setFromPoolWorker()

        if self.provider is None or self.pool is None:
            # If the config changed out from underneath us, we could now be
            # an invalid provider and should stop handling this request.
            raise Exception("Provider configuration missing")

        # We have the launcher_id attr after _setFromPoolWorker() is called.
        self.log = logging.getLogger(
            "nodepool.driver.NodeRequestHandler[%s]" % self.launcher_id)

        declined_reasons = []
        invalid_types = self._invalidNodeTypes()

        if self.pool.max_servers <= 0:
            declined_reasons.append('pool is disabled by max_servers')
        elif invalid_types:
            declined_reasons.append('node type(s) [%s] not available' %
                                    ','.join(invalid_types))
        elif not self.imagesAvailable():
            declined_reasons.append('images are not available')
        elif not self.hasProviderQuota(self.request.node_types):
            declined_reasons.append('it would exceed quota')

        if declined_reasons:
            self.log.debug("Declining node request %s because %s",
                           self.request.id, ', '.join(declined_reasons))
            self.decline_request()
            self._declinedHandlerCleanup()
            return

        if self.paused:
            self.log.debug("Retrying node request %s", self.request.id)
        else:
            self.log.debug("Accepting node request %s", self.request.id)
            self.request.state = zk.PENDING
            self.zk.storeNodeRequest(self.request)

        self._waitForNodeSet()

    def _declinedHandlerCleanup(self):
        """
        After declining a request, do necessary cleanup actions.
        """
        self.unlockNodeSet(clear_allocation=True)

        # If conditions have changed for a paused request to now cause us
        # to decline it, we need to unpause so we don't keep trying it
        if self.paused:
            self.paused = False

        try:
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
        except Exception:
            # If the request is gone for some reason, we need to make
            # sure that self.done still gets set.
            self.log.exception("Unable to modify missing request %s",
                               self.request.id)
        self.done = True

    # ---------------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------------

    def unlockNodeSet(self, clear_allocation=False):
        '''
        Attempt unlocking all Nodes in the node set.

        :param bool clear_allocation: If true, clears the node allocated_to
            attribute.
        '''
        for node in self.nodeset:
            if not node.lock:
                continue

            if clear_allocation:
                node.allocated_to = None
                self.zk.storeNode(node)

            try:
                self.zk.unlockNode(node)
            except Exception:
                self.log.exception("Error unlocking node:")
            self.log.debug("Unlocked node %s for request %s",
                           node.id, self.request.id)

        self.nodeset = []

    def decline_request(self):
        # Technically, this check to see if we've already declined it should
        # not be necessary. But if there is a bug (and there has been), we
        # want to make sure we don't continuously grow this array.
        if self.launcher_id not in self.request.declined_by:
            self.request.declined_by.append(self.launcher_id)
        launchers = set([x.id for x in self.zk.getRegisteredLaunchers()])
        if launchers.issubset(set(self.request.declined_by)):
            # All launchers have declined it
            self.log.debug("Failing declined node request %s",
                           self.request.id)
            self.request.state = zk.FAILED
        else:
            self.request.state = zk.REQUESTED

    def run(self):
        '''
        Execute node request handling.

        This code is designed to be re-entrant. Because we can't always
        satisfy a request immediately (due to lack of provider resources), we
        need to be able to call run() repeatedly until the request can be
        fulfilled. The node set is saved and added to between calls.
        '''
        try:
            self._runHandler()
        except Exception:
            self.log.exception(
                "Declining node request %s due to exception in "
                "NodeRequestHandler:", self.request.id)
            self.decline_request()
            self._declinedHandlerCleanup()

    def poll(self):
        if self.paused:
            return False

        if self.done:
            return True

        # Driver must implement this call
        if not self.launchesComplete():
            return False

        # Launches are complete, so populate ready_nodes and failed_nodes.
        aborted_nodes = []
        for node in self.nodeset.copy():
            if node.state == zk.READY:
                self.ready_nodes.append(node)
            elif node.state == zk.ABORTED:
                # ABORTED is a transient error triggered by overquota. In order
                # to handle this gracefully don't count this as failed so the
                # node is relaunched within this provider. Unlock the node so
                # the DeletedNodeWorker cleans up the zNode.
                aborted_nodes.append(node)
                self.nodeset.remove(node)
                self.zk.unlockNode(node)
            else:
                self.failed_nodes.append(node)

        # If the request has been pulled, unallocate the node set so other
        # requests can use them.
        if not self.zk.getNodeRequest(self.request.id):
            self.log.info("Node request %s disappeared", self.request.id)
            for node in self.nodeset:
                node.allocated_to = None
                self.zk.storeNode(node)
            self.unlockNodeSet()
            try:
                self.zk.unlockNodeRequest(self.request)
            except exceptions.ZKLockException:
                # If the lock object is invalid that is "ok" since we no
                # longer have a request either. Just do our best, log and
                # move on.
                self.log.debug("Request lock invalid for node request %s "
                               "when attempting to clean up the lock",
                               self.request.id)
            return True

        if self.failed_nodes:
            self.log.debug("Declining node request %s because nodes failed",
                           self.request.id)
            self.decline_request()
        elif aborted_nodes:
            # Because nodes are added to the satisfied types list before they
            # are ready we need to remove the aborted nodes again so they can
            # be created again.
            for node in aborted_nodes:
                self._satisfied_types.removeNode(node.id)
            self.log.debug(
                "Pausing request handling after node abort to satisfy "
                "request %s", self.request.id)
            self.paused = True
            return False
        else:
            # The assigned nodes must be added to the request in the order
            # in which they were requested.
            for requested_type in self.request.node_types:
                node_id = self._satisfied_types.pop(requested_type)
                self.request.nodes.append(node_id)

            self.log.debug("Fulfilled node request %s",
                           self.request.id)
            self.request.state = zk.FULFILLED

        self.unlockNodeSet()
        self.zk.storeNodeRequest(self.request)
        self.zk.unlockNodeRequest(self.request)
        return True

    # ---------------------------------------------------------------
    # Driver Implementation
    # ---------------------------------------------------------------

    def hasProviderQuota(self, node_types):
        '''
        Checks if a provider has enough quota to handle a list of nodes.
        This does not take our currently existing nodes into account.

        :param node_types: list of node types to check
        :return: True if the node list fits into the provider, False otherwise
        '''
        return True

    def hasRemainingQuota(self, ntype):
        '''
        Checks if the predicted quota is enough for an additional node of type
        ntype.

        :param ntype: node type for the quota check
        :return: True if there is enough quota, False otherwise
        '''
        return True

    def checkReusableNode(self, node):
        '''
        Handler may implement this to verify a node can be re-used.
        The OpenStack handler uses this to verify the node az is correct.
        '''
        return True

    def setNodeMetadata(self, node):
        '''
        Handler may implement this to store driver-specific metadata in the
        Node object before building the node. This data is normally dynamically
        calculated during runtime. The OpenStack handler uses this to set az,
        cloud and region.
        '''
        pass

    @property
    @abc.abstractmethod
    def alive_thread_count(self):
        '''
        Return the number of active node launching threads in use by this
        request handler.

        This is used to limit request handling threads for a provider.

        This is an approximate, top-end number for alive threads, since some
        threads obviously may have finished by the time we finish the
        calculation.

        :returns: A count (integer) of active threads.
        '''
        pass

    @abc.abstractmethod
    def imagesAvailable(self):
        '''
        Handler needs to implement this to determines if the requested images
        in self.request.node_types are available for this provider.

        :returns: True if it is available, False otherwise.
        '''
        pass

    @abc.abstractmethod
    def launch(self, node):
        '''
        Handler needs to implement this to launch the node.
        '''
        pass

    @abc.abstractmethod
    def launchesComplete(self):
        '''
        Handler needs to implement this to check if all nodes in self.nodeset
        have completed the launch sequence..

        This method will be called periodically to check on launch progress.

        :returns: True if all launches are complete (successfully or not),
            False otherwise.
        '''
        pass


class ConfigValue(object, metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def __eq__(self, other):
        pass

    def __ne__(self, other):
        return not self.__eq__(other)


class ConfigPool(ConfigValue, metaclass=abc.ABCMeta):
    '''
    Base class for a single pool as defined in the configuration file.
    '''

    def __init__(self):
        self.labels = {}
        self.max_servers = math.inf
        self.node_attributes = None

    def __eq__(self, other):
        if isinstance(other, ConfigPool):
            return (self.labels == other.labels and
                    self.max_servers == other.max_servers and
                    self.node_attributes == other.node_attributes)
        return False

    @classmethod
    def getCommonSchemaDict(self):
        '''
        Return the schema dict for common pool attributes.

        When a driver validates its own configuration schema, it should call
        this class method to get and include the common pool attributes in
        the schema.

        The `labels` attribute, though common, can vary its type across
        drivers so it is not returned in the schema.
        '''
        return {
            'max-servers': int,
            'node-attributes': dict,
        }

    @abc.abstractmethod
    def load(self, pool_config):
        '''
        Load pool config options from the parsed configuration file.

        Subclasses are expected to call the parent method so that common
        configuration values are loaded properly.

        Although `labels` is a common attribute, each driver may
        define it differently, so we cannot parse that attribute here.

        :param dict pool_config: A single pool config section from which we
            will load the values.
        '''
        self.max_servers = pool_config.get('max-servers', math.inf)
        self.node_attributes = pool_config.get('node-attributes')


class DriverConfig(ConfigValue):
    def __init__(self):
        self.name = None

    def __eq__(self, other):
        if isinstance(other, DriverConfig):
            return self.name == other.name
        return False


class ProviderConfig(ConfigValue, metaclass=abc.ABCMeta):
    """The Provider config interface

    The class or instance attribute **name** must be provided as a string.

    """
    def __init__(self, provider):
        self.name = provider['name']
        self.provider = provider
        self.driver = DriverConfig()
        self.driver.name = provider.get('driver', 'openstack')
        self.max_concurrency = provider.get('max-concurrency', -1)

    def __eq__(self, other):
        if isinstance(other, ProviderConfig):
            return (self.name == other.name and
                    self.provider == other.provider and
                    self.driver == other.driver and
                    self.max_concurrency == other.max_concurrency)
        return False

    def __repr__(self):
        return "<Provider %s>" % self.name

    @classmethod
    def getCommonSchemaDict(self):
        return {
            v.Required('name'): str,
            'driver': str,
            'max-concurrency': int
        }

    @property
    @abc.abstractmethod
    def pools(self):
        '''
        Return a dict of ConfigPool-based objects, indexed by pool name.
        '''
        pass

    @property
    @abc.abstractmethod
    def manage_images(self):
        '''
        Return True if provider manages external images, False otherwise.
        '''
        pass

    @abc.abstractmethod
    def load(self, newconfig):
        '''
        Update this config object from the supplied parsed config
        '''
        pass

    @abc.abstractmethod
    def getSchema(self):
        '''
        Return a voluptuous schema for config validation
        '''
        pass

    @abc.abstractmethod
    def getSupportedLabels(self, pool_name=None):
        '''
        Return a set of label names supported by this provider.

        :param str pool_name: If provided, get labels for the given pool only.
        '''
        pass
