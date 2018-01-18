#!/usr/bin/env python
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

from contextlib import contextmanager
from copy import copy
import json
import logging
import six
import time
from kazoo.client import KazooClient, KazooState
from kazoo import exceptions as kze
from kazoo.recipe.lock import Lock

from nodepool import exceptions as npe

# States:
# We are building this image (or node) but it is not ready for use.
BUILDING = 'building'
# The image is being uploaded.
UPLOADING = 'uploading'
# The image/upload/node is ready for use.
READY = 'ready'
# The image/upload/node should be deleted.
DELETING = 'deleting'
# The build failed.
FAILED = 'failed'
# Node request is submitted/unhandled.
REQUESTED = 'requested'
# Node request has been processed successfully.
FULFILLED = 'fulfilled'
# Node request is being worked.
PENDING = 'pending'
# Node is being tested
TESTING = 'testing'
# Node is being used
IN_USE = 'in-use'
# Node has been used
USED = 'used'
# Node is being held
HOLD = 'hold'
# Initial node state
INIT = 'init'


class ZooKeeperConnectionConfig(object):
    '''
    Represents the connection parameters for a ZooKeeper server.
    '''

    def __eq__(self, other):
        if isinstance(other, ZooKeeperConnectionConfig):
            if other.__dict__ == self.__dict__:
                return True
        return False

    def __init__(self, host, port=2181, chroot=None):
        '''Initialize the ZooKeeperConnectionConfig object.

        :param str host: The hostname of the ZooKeeper server.
        :param int port: The port on which ZooKeeper is listening.
            Optional, default: 2181.
        :param str chroot: A chroot for this connection.  All
            ZooKeeper nodes will be underneath this root path.
            Optional, default: None.

        (one per server) defining the ZooKeeper cluster servers. Only
        the 'host' attribute is required.'.

        '''
        self.host = host
        self.port = port
        self.chroot = chroot or ''


def buildZooKeeperHosts(host_list):
    '''
    Build the ZK cluster host list for client connections.

    :param list host_list: A list of
        :py:class:`~nodepool.zk.ZooKeeperConnectionConfig` objects (one
        per server) defining the ZooKeeper cluster servers.
    '''
    if not isinstance(host_list, list):
        raise Exception("'host_list' must be a list")
    hosts = []
    for host_def in host_list:
        host = '%s:%s%s' % (host_def.host, host_def.port, host_def.chroot)
        hosts.append(host)
    return ",".join(hosts)


class ZooKeeperWatchEvent(object):
    '''
    Class representing a watch trigger event.

    This is mostly used to not pass the kazoo event object to the function
    registered by the caller and maintain a consistent API. Attributes of
    this object include::

        * `type` - Event type. E.g., CREATED/DELETED
        * `state` - Event state. E.g., CONNECTED
        * `path` - Event path. E.g., /nodepool/image/trusty/request-build
        * `image` - Image name this event is based on. E.g., trusty
    '''
    def __init__(self, e_type, e_state, e_path, image):
        self.type = e_type
        self.state = e_state
        self.path = e_path
        # Pass image name so callback func doesn't need to parse from the path
        self.image = image


class BaseModel(object):
    VALID_STATES = set([])

    def __init__(self, o_id):
        if o_id:
            # Call the setter for id so we can validate the incoming type.
            self.id = o_id
        else:
            # Bypass the setter for id to set the default.
            self._id = None
        self._state = None
        self.state_time = None
        self.stat = None

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        if not isinstance(value, six.string_types):
            raise TypeError("'id' attribute must be a string type")
        self._id = value

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value not in self.VALID_STATES:
            raise TypeError("'%s' is not a valid state" % value)
        self._state = value
        self.state_time = time.time()

    def toDict(self):
        '''
        Convert a BaseModel object's attributes to a dictionary.
        '''
        d = {}
        d['state'] = self.state
        d['state_time'] = self.state_time
        return d

    def fromDict(self, d):
        '''
        Set base attributes based on the given dict.

        Unlike the derived classes, this should NOT return an object as it
        assumes self has already been instantiated.
        '''
        if 'state' in d:
            self.state = d['state']
        if 'state_time' in d:
            self.state_time = d['state_time']

    def serialize(self):
        '''
        Return a representation of the object as a string.

        Used for storing the object data in ZooKeeper.
        '''
        return json.dumps(self.toDict()).encode('utf8')


class ImageBuild(BaseModel):
    '''
    Class representing a DIB image build within the ZooKeeper cluster.

    Note that the 'builder' attribute used to be used to uniquely identify
    the owner of an image build in ZooKeeper. Because hostname was used, if
    it ever changed, then we would get orphaned znodes. The 'builder_id'
    attribute was added as a replacement, keeping 'builder' to mean the
    same thing (which is why this attribute is not called 'hostname' or
    similar).
    '''
    VALID_STATES = set([BUILDING, READY, DELETING, FAILED])

    def __init__(self, build_id=None):
        super(ImageBuild, self).__init__(build_id)
        self._formats = []
        self.builder = None       # Hostname
        self.builder_id = None    # Unique ID
        self.username = None

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['stat'] = self.stat
        return '<ImageBuild %s>' % d

    @property
    def formats(self):
        return self._formats

    @formats.setter
    def formats(self, value):
        if not isinstance(value, list):
            raise TypeError("'formats' attribute must be a list type")
        self._formats = copy(value)

    def addFormat(self, fmt):
        self._formats.append(fmt)

    def toDict(self):
        '''
        Convert an ImageBuild object's attributes to a dictionary.
        '''
        d = super(ImageBuild, self).toDict()
        if self.builder is not None:
            d['builder'] = self.builder
        if self.builder_id is not None:
            d['builder_id'] = self.builder_id
        if len(self.formats):
            d['formats'] = ','.join(self.formats)
        d['username'] = self.username
        return d

    @staticmethod
    def fromDict(d, o_id=None):
        '''
        Create an ImageBuild object from a dictionary.

        :param dict d: The dictionary.
        :param str o_id: The object ID.

        :returns: An initialized ImageBuild object.
        '''
        o = ImageBuild(o_id)
        super(ImageBuild, o).fromDict(d)
        o.builder = d.get('builder')
        o.builder_id = d.get('builder_id')
        o.username = d.get('username', 'zuul')
        # Only attempt the split on non-empty string
        if d.get('formats', ''):
            o.formats = d.get('formats', '').split(',')
        return o


class ImageUpload(BaseModel):
    '''
    Class representing a provider image upload within the ZooKeeper cluster.
    '''
    VALID_STATES = set([UPLOADING, READY, DELETING, FAILED])

    def __init__(self, build_id=None, provider_name=None, image_name=None,
                 upload_id=None, username=None):
        super(ImageUpload, self).__init__(upload_id)
        self.build_id = build_id
        self.provider_name = provider_name
        self.image_name = image_name
        self.format = None
        self.username = username
        self.external_id = None      # Provider ID of the image
        self.external_name = None    # Provider name of the image

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['build_id'] = self.build_id
        d['provider_name'] = self.provider_name
        d['image_name'] = self.image_name
        d['format'] = self.format
        d['stat'] = self.stat
        return '<ImageUpload %s>' % d

    def __eq__(self, other):
        if isinstance(other, ImageUpload):
            return (self.id == other.id and
                    self.provider_name == other.provider_name and
                    self.build_id == other.build_id and
                    self.image_name == other.image_name and
                    self.format == other.format)
        else:
            return False

    def toDict(self):
        '''
        Convert an ImageUpload object's attributes to a dictionary.
        '''
        d = super(ImageUpload, self).toDict()
        d['external_id'] = self.external_id
        d['external_name'] = self.external_name
        d['format'] = self.format
        d['username'] = self.username
        return d

    @staticmethod
    def fromDict(d, build_id, provider_name, image_name, upload_id):
        '''
        Create an ImageUpload object from a dictionary.

        :param dict d: The dictionary.
        :param str build_id: The build ID.
        :param str provider_name: The provider name.
        :param str image_name: The image name.
        :param str upload_id: The upload ID.

        :returns: An initialized ImageUpload object.
        '''
        o = ImageUpload(build_id, provider_name, image_name, upload_id)
        super(ImageUpload, o).fromDict(d)
        o.external_id = d.get('external_id')
        o.external_name = d.get('external_name')
        o.format = d.get('format')
        o.username = d.get('username', 'zuul')
        return o


class NodeRequestLockStats(object):
    '''
    Class holding the stats of a node request lock znode.

    This doesn't need to derive from BaseModel since this class exists only
    to associate the znode stats with the lock.
    '''
    def __init__(self, lock_id=None):
        self.lock_id = lock_id
        self.stat = None

    def __eq__(self, other):
        if isinstance(other, NodeRequestLockStats):
            return (self.lock_id == other.lock_id)
        else:
            return False

    def __repr__(self):
        return '<NodeRequestLockStats %s>' % self.lock_id


class NodeRequest(BaseModel):
    '''
    Class representing a node request.
    '''
    VALID_STATES = set([REQUESTED, PENDING, FULFILLED, FAILED])

    def __init__(self, id=None):
        super(NodeRequest, self).__init__(id)
        self.lock = None
        self.declined_by = []
        self.node_types = []
        self.nodes = []
        self.reuse = True
        self.requestor = None

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['stat'] = self.stat
        return '<NodeRequest %s>' % d

    def __eq__(self, other):
        if isinstance(other, NodeRequest):
            return (self.id == other.id and
                    self.declined_by == other.declined_by and
                    self.node_types == other.node_types and
                    self.nodes == other.nodes and
                    self.reuse == other.reuse and
                    self.requestor == other.requestor)
        else:
            return False

    def toDict(self):
        '''
        Convert a NodeRequest object's attributes to a dictionary.
        '''
        d = super(NodeRequest, self).toDict()
        d['declined_by'] = self.declined_by
        d['node_types'] = self.node_types
        d['nodes'] = self.nodes
        d['reuse'] = self.reuse
        d['requestor'] = self.requestor
        return d

    @staticmethod
    def fromDict(d, o_id=None):
        '''
        Create a NodeRequest object from a dictionary.

        :param dict d: The dictionary.
        :param str o_id: The object ID.

        :returns: An initialized NodeRequest object.
        '''
        o = NodeRequest(o_id)
        super(NodeRequest, o).fromDict(d)
        o.declined_by = d.get('declined_by', [])
        o.node_types = d.get('node_types', [])
        o.nodes = d.get('nodes', [])
        o.reuse = d.get('reuse', True)
        o.requestor = d.get('requestor')
        return o


class Node(BaseModel):
    '''
    Class representing a launched node.
    '''
    VALID_STATES = set([BUILDING, TESTING, READY, IN_USE, USED,
                        HOLD, DELETING, FAILED, INIT])

    def __init__(self, id=None):
        super(Node, self).__init__(id)
        self.lock = None
        self.cloud = None
        self.provider = None
        self.pool = None
        self.type = None
        self.allocated_to = None
        self.az = None
        self.region = None
        self.public_ipv4 = None
        self.private_ipv4 = None
        self.public_ipv6 = None
        self.interface_ip = None
        self.connection_port = 22
        self.image_id = None
        self.launcher = None
        self.created_time = None
        self.external_id = None
        self.hostname = None
        self.comment = None
        self.hold_job = None
        self.username = None
        self.connection_type = None
        self.host_keys = []

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['stat'] = self.stat
        return '<Node %s>' % d

    def __eq__(self, other):
        if isinstance(other, Node):
            return (self.id == other.id and
                    self.cloud == other.cloud and
                    self.state == other.state and
                    self.state_time == other.state_time and
                    self.provider == other.provider and
                    self.pool == other.pool and
                    self.type == other.type and
                    self.allocated_to == other.allocated_to and
                    self.az == other.az and
                    self.region == other.region and
                    self.public_ipv4 == other.public_ipv4 and
                    self.private_ipv4 == other.private_ipv4 and
                    self.public_ipv6 == other.public_ipv6 and
                    self.interface_ip == other.interface_ip and
                    self.image_id == other.image_id and
                    self.launcher == other.launcher and
                    self.created_time == other.created_time and
                    self.external_id == other.external_id and
                    self.hostname == other.hostname and
                    self.comment == other.comment and
                    self.hold_job == other.hold_job and
                    self.username == other.username and
                    self.connection_type == other.connection_type and
                    self.host_keys == other.host_keys)
        else:
            return False

    def toDict(self):
        '''
        Convert a Node object's attributes to a dictionary.
        '''
        d = super(Node, self).toDict()
        d['cloud'] = self.cloud
        d['provider'] = self.provider
        d['pool'] = self.pool
        d['type'] = self.type
        d['allocated_to'] = self.allocated_to
        d['az'] = self.az
        d['region'] = self.region
        d['public_ipv4'] = self.public_ipv4
        d['private_ipv4'] = self.private_ipv4
        d['public_ipv6'] = self.public_ipv6
        d['interface_ip'] = self.interface_ip
        d['connection_port'] = self.connection_port
        # TODO(tobiash): ssh_port is kept for backwards compatibility reasons
        # to zuul. It should be removed after some deprecation time.
        d['ssh_port'] = self.connection_port
        d['image_id'] = self.image_id
        d['launcher'] = self.launcher
        d['created_time'] = self.created_time
        d['external_id'] = self.external_id
        d['hostname'] = self.hostname
        d['comment'] = self.comment
        d['hold_job'] = self.hold_job
        d['host_keys'] = self.host_keys
        d['username'] = self.username
        d['connection_type'] = self.connection_type
        return d

    @staticmethod
    def fromDict(d, o_id=None):
        '''
        Create a Node object from a dictionary.

        :param dict d: The dictionary.
        :param str o_id: The object ID.

        :returns: An initialized Node object.
        '''
        o = Node(o_id)
        super(Node, o).fromDict(d)
        o.cloud = d.get('cloud')
        o.provider = d.get('provider')
        o.pool = d.get('pool')
        o.type = d.get('type')
        o.allocated_to = d.get('allocated_to')
        o.az = d.get('az')
        o.region = d.get('region')
        o.public_ipv4 = d.get('public_ipv4')
        o.private_ipv4 = d.get('private_ipv4')
        o.public_ipv6 = d.get('public_ipv6')
        o.interface_ip = d.get('interface_ip')
        o.connection_port = d.get('connection_port', d.get('ssh_port', 22))
        o.image_id = d.get('image_id')
        o.launcher = d.get('launcher')
        o.created_time = d.get('created_time')
        o.external_id = d.get('external_id')
        o.hostname = d.get('hostname')
        o.comment = d.get('comment')
        o.hold_job = d.get('hold_job')
        o.username = d.get('username', 'zuul')
        o.connection_type = d.get('connection_type')
        o.host_keys = d.get('host_keys', [])
        return o


class ZooKeeper(object):
    '''
    Class implementing the ZooKeeper interface.

    This class uses the facade design pattern to keep common interaction
    with the ZooKeeper API simple and consistent for the caller, and
    limits coupling between objects. It allows for more complex interactions
    by providing direct access to the client connection when needed (though
    that is discouraged). It also provides for a convenient entry point for
    testing only ZooKeeper interactions.

    Most API calls reference an image name only, as the path for the znode
    for that image is calculated automatically. And image names are assumed
    to be unique.
    '''

    log = logging.getLogger("nodepool.zk.ZooKeeper")

    IMAGE_ROOT = "/nodepool/images"
    LAUNCHER_ROOT = "/nodepool/launchers"
    NODE_ROOT = "/nodepool/nodes"
    REQUEST_ROOT = "/nodepool/requests"
    REQUEST_LOCK_ROOT = "/nodepool/requests-lock"

    def __init__(self):
        '''
        Initialize the ZooKeeper object.
        '''
        self.client = None
        self._became_lost = False

    # =======================================================================
    # Private Methods
    # =======================================================================

    def _imagePath(self, image):
        return "%s/%s" % (self.IMAGE_ROOT, image)

    def _imageBuildRequestPath(self, image):
        return "%s/request-build" % self._imagePath(image)

    def _imageBuildsPath(self, image):
        return "%s/builds" % self._imagePath(image)

    def _imageBuildLockPath(self, image):
        return "%s/lock" % self._imageBuildsPath(image)

    def _imageBuildNumberLockPath(self, image, build_number):
        return "%s/%s/lock" % (self._imageBuildsPath(image),
                               build_number)

    def _imageProviderPath(self, image, build_number):
        return "%s/%s/providers" % (self._imageBuildsPath(image),
                                    build_number)

    def _imageUploadPath(self, image, build_number, provider):
        return "%s/%s/providers/%s/images" % (self._imageBuildsPath(image),
                                              build_number,
                                              provider)

    def _imageUploadLockPath(self, image, build_number, provider):
        return "%s/lock" % self._imageUploadPath(image, build_number,
                                                 provider)

    def _launcherPath(self, launcher):
        return "%s/%s" % (self.LAUNCHER_ROOT, launcher)

    def _nodePath(self, node):
        return "%s/%s" % (self.NODE_ROOT, node)

    def _nodeLockPath(self, node):
        return "%s/%s/lock" % (self.NODE_ROOT, node)

    def _requestPath(self, request):
        return "%s/%s" % (self.REQUEST_ROOT, request)

    def _requestLockPath(self, request):
        return "%s/%s" % (self.REQUEST_LOCK_ROOT, request)

    def _bytesToDict(self, data):
        return json.loads(data.decode('utf8'))

    def _getImageBuildLock(self, image, blocking=True, timeout=None):
        lock_path = self._imageBuildLockPath(image)
        try:
            lock = Lock(self.client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image build not found for locking: %s", image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def _getImageBuildNumberLock(self, image, build_number,
                                 blocking=True, timeout=None):
        lock_path = self._imageBuildNumberLockPath(image, build_number)
        try:
            lock = Lock(self.client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image build number not found for locking: %s, %s",
                           build_number, image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def _getImageUploadLock(self, image, build_number, provider,
                            blocking=True, timeout=None):
        lock_path = self._imageUploadLockPath(image, build_number, provider)
        try:
            lock = Lock(self.client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image upload not found for locking: %s, %s, %s",
                           build_number, provider, image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def _connection_listener(self, state):
        '''
        Listener method for Kazoo connection state changes.

        .. warning:: This method must not block.
        '''
        if state == KazooState.LOST:
            self.log.debug("ZooKeeper connection: LOST")
            self._became_lost = True
        elif state == KazooState.SUSPENDED:
            self.log.debug("ZooKeeper connection: SUSPENDED")
        else:
            self.log.debug("ZooKeeper connection: CONNECTED")

    # =======================================================================
    # Public Methods and Properties
    # =======================================================================

    @property
    def connected(self):
        if self.client is None:
            return False
        return self.client.state == KazooState.CONNECTED

    @property
    def suspended(self):
        if self.client is None:
            return True
        return self.client.state == KazooState.SUSPENDED

    @property
    def lost(self):
        if self.client is None:
            return True
        return self.client.state == KazooState.LOST

    @property
    def didLoseConnection(self):
        return self._became_lost

    def resetLostFlag(self):
        self._became_lost = False

    def connect(self, host_list, read_only=False):
        '''
        Establish a connection with ZooKeeper cluster.

        Convenience method if a pre-existing ZooKeeper connection is not
        supplied to the ZooKeeper object at instantiation time.

        :param list host_list: A list of
            :py:class:`~nodepool.zk.ZooKeeperConnectionConfig` objects
            (one per server) defining the ZooKeeper cluster servers.
        :param bool read_only: If True, establishes a read-only connection.

        '''
        if self.client is None:
            hosts = buildZooKeeperHosts(host_list)
            self.client = KazooClient(hosts=hosts, read_only=read_only)
            self.client.add_listener(self._connection_listener)
            self.client.start()

    def disconnect(self):
        '''
        Close the ZooKeeper cluster connection.

        You should call this method if you used connect() to establish a
        cluster connection.
        '''
        if self.client is not None and self.client.connected:
            self.client.stop()
            self.client.close()
            self.client = None

    def resetHosts(self, host_list):
        '''
        Reset the ZooKeeper cluster connection host list.

        :param list host_list: A list of
            :py:class:`~nodepool.zk.ZooKeeperConnectionConfig` objects
            (one per server) defining the ZooKeeper cluster servers.
        '''
        if self.client is not None:
            hosts = buildZooKeeperHosts(host_list)
            self.client.set_hosts(hosts=hosts)

    @contextmanager
    def imageBuildLock(self, image, blocking=True, timeout=None):
        '''
        Context manager to use for locking image builds.

        Obtains a write lock for the specified image.

        :param str image: Name of the image to lock
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageBuildLock(image, blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    @contextmanager
    def imageBuildNumberLock(self, image, build_number,
                             blocking=True, timeout=None):
        '''
        Context manager to use for locking _specific_ image builds.

        Obtains a write lock for the specified image build number. This is
        used for locking a build number during the cleanup phase of the
        builder.

        :param str image: Name of the image
        :param str build_number: The image build number to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageBuildNumberLock(image, build_number,
                                                 blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    @contextmanager
    def imageUploadLock(self, image, build_number, provider,
                        blocking=True, timeout=None):
        '''
        Context manager to use for locking image builds.

        Obtains a write lock for the specified image upload.

        :param str image: Name of the image.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageUploadLock(image, build_number, provider,
                                            blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    def getImageNames(self):
        '''
        Retrieve the image names in Zookeeper.

        :returns: A list of image names or the empty list.
        '''
        path = self.IMAGE_ROOT

        try:
            images = self.client.get_children(path)
        except kze.NoNodeError:
            return []
        return sorted(images)

    def getBuildNumbers(self, image):
        '''
        Retrieve the builds available for an image.

        :param str image: The image name.

        :returns: A list of image build numbers or the empty list.
        '''
        path = self._imageBuildsPath(image)

        try:
            builds = self.client.get_children(path)
        except kze.NoNodeError:
            return []
        builds = [x for x in builds if x != 'lock']
        return builds

    def getBuildProviders(self, image, build_number):
        '''
        Retrieve the providers which have uploads for an image build.

        :param str image: The image name.
        :param str build_number: The image build number.

        :returns: A list of provider names or the empty list.

        '''
        path = self._imageProviderPath(image, build_number)

        try:
            providers = self.client.get_children(path)
        except kze.NoNodeError:
            return []

        return sorted(providers)

    def getImageUploadNumbers(self, image, build_number, provider):
        '''
        Retrieve upload numbers for a provider and image build.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.

        :returns: A list of upload numbers or the empty list.

        '''
        path = self._imageUploadPath(image, build_number, provider)

        try:
            uploads = self.client.get_children(path)
        except kze.NoNodeError:
            return []

        uploads = [x for x in uploads if x != 'lock']
        return uploads

    def getBuild(self, image, build_number):
        '''
        Retrieve the image build data.

        :param str image: The image name.
        :param str build_number: The image build number.

        :returns: An ImageBuild object, or None if not found.
        '''
        path = self._imageBuildsPath(image) + "/%s" % build_number

        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None

        d = ImageBuild.fromDict(self._bytesToDict(data), build_number)
        d.stat = stat
        return d

    def getBuilds(self, image, states=None):
        '''
        Retrieve all image build data matching any given states.

        :param str image: The image name.
        :param list states: A list of build state values to match against.
            A value of None will disable state matching and just return
            all builds.

        :returns: A list of ImageBuild objects.
        '''
        path = self._imageBuildsPath(image)

        try:
            builds = self.client.get_children(path)
        except kze.NoNodeError:
            return []

        matches = []
        for build in builds:
            if build == 'lock':   # skip the build lock node
                continue
            data = self.getBuild(image, build)
            if states is None:
                matches.append(data)
            elif data and data.state in states:
                matches.append(data)

        return matches

    def getMostRecentBuilds(self, count, image, state=None):
        '''
        Retrieve the most recent image build data with the given state.

        :param int count: A count of the most recent builds to return.
            Use None for all builds.
        :param str image: The image name.
        :param str state: The build state to match on. Use None to
            ignore state

        :returns: A list of the most recent ImageBuild objects matching the
            given state, or an empty list if there were no builds matching
            the state. You may get less than 'count' entries if there were
            not enough matching builds.
        '''
        states = None
        if state:
            states = [state]

        builds = self.getBuilds(image, states)
        if not builds:
            return []

        builds.sort(key=lambda x: x.state_time, reverse=True)
        return builds[:count]

    def storeBuild(self, image, build_data, build_number=None):
        '''
        Store the image build data.

        The build data is either created if it does not exist, or it is
        updated in its entirety if it does not. There is no partial updating.
        The build data is expected to be represented as a dict. This dict may
        contain any data, as appropriate.

        If a build number is not supplied, then a new build node/number is
        created. The new build number is available in the return value.

        .. important: You should have the image locked before calling this
            method.

        :param str image: The image name for which we have data.
        :param ImageBuild build_data: The build data.
        :param str build_number: The image build number.

        :returns: A string for the build number that was updated.
        '''
        # Append trailing / so the sequence node is created as a child node.
        build_path = self._imageBuildsPath(image) + "/"

        if build_number is None:
            path = self.client.create(
                build_path,
                value=build_data.serialize(),
                sequence=True,
                makepath=True)
            build_number = path.split("/")[-1]
        else:
            path = build_path + build_number
            self.client.set(path, build_data.serialize())

        return build_number

    def getImageUpload(self, image, build_number, provider, upload_number):
        '''
        Retrieve the image upload data.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param str upload_number: The image upload number.

        :returns: An ImageUpload object, or None if not found.

        :raises: ZKException if the image upload path is not found.
        '''
        path = self._imageUploadPath(image, build_number, provider)
        path = path + "/%s" % upload_number

        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None

        d = ImageUpload.fromDict(self._bytesToDict(data),
                                 build_number,
                                 provider,
                                 image,
                                 upload_number)
        d.stat = stat
        return d

    def getUploads(self, image, build_number, provider, states=None):
        '''
        Retrieve all image upload data matching any given states.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param list states: A list of upload state values to match against.
            A value of None will disable state matching and just return
            all uploads.

        :returns: A list of ImageUpload objects.
        '''
        path = self._imageUploadPath(image, build_number, provider)

        try:
            uploads = self.client.get_children(path)
        except kze.NoNodeError:
            return []

        matches = []
        for upload in uploads:
            if upload == 'lock':
                continue
            data = self.getImageUpload(image, build_number, provider, upload)
            if not data:
                continue
            if states is None:
                matches.append(data)
            elif data.state in states:
                matches.append(data)

        return matches

    def getMostRecentBuildImageUploads(self, count, image, build_number,
                                       provider, state=None):
        '''
        Retrieve the most recent image upload data with the given state.

        :param int count: A count of the most recent uploads to return.
            Use None for all uploads.
        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param str state: The image upload state to match on. Use None to
            ignore state.

        :returns: A tuple with the most recent upload number and dictionary of
            upload data matching the given state, or None if there was no
            upload matching the state.
        '''
        states = None
        if state:
            states = [state]

        uploads = self.getUploads(image, build_number, provider, states)
        if not uploads:
            return []

        uploads.sort(key=lambda x: x.state_time, reverse=True)
        return uploads[:count]

    def getMostRecentImageUpload(self, image, provider,
                                 state=READY):
        '''
        Retrieve the most recent image upload data with the given state.

        :param str image: The image name.
        :param str provider: The provider name owning the image.
        :param str state: The image upload state to match on.

        :returns: An ImageUpload object matching the given state, or
            None if there is no recent upload.
        '''

        recent_data = None
        for build_number in self.getBuildNumbers(image):
            path = self._imageUploadPath(image, build_number, provider)

            try:
                uploads = self.client.get_children(path)
            except kze.NoNodeError:
                uploads = []

            for upload in uploads:
                if upload == 'lock':   # skip the upload lock node
                    continue
                data = self.getImageUpload(
                    image, build_number, provider, upload)
                if not data or data.state != state:
                    continue
                elif (recent_data is None or
                      recent_data.state_time < data.state_time):
                    recent_data = data

        return recent_data

    def storeImageUpload(self, image, build_number, provider, image_data,
                         upload_number=None):
        '''
        Store the built image's upload data for the given provider.

        The image upload data is either created if it does not exist, or it
        is updated in its entirety if it does not. There is no partial
        updating. The image data is expected to be represented as a dict.
        This dict may contain any data, as appropriate.

        If an image upload number is not supplied, then a new image upload
        node/number is created. The new upload number is available in the
        return value.

        :param str image: The image name for which we have data.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param ImageUpload image_data: The image data we want to store.
        :param str upload_number: The image upload number to update.

        :returns: A string for the upload number that was updated.

        :raises: ZKException for an invalid image build.
        '''
        # We expect the image builds path to already exist.
        build_path = self._imageBuildsPath(image)
        if not self.client.exists(build_path):
            raise npe.ZKException(
                "Cannot find build %s of image %s" % (build_number, image)
            )

        # Generate a path for the upload. This doesn't have to exist yet
        # since we'll create new provider/upload ID znodes automatically.
        # Append trailing / so the sequence node is created as a child node.
        upload_path = self._imageUploadPath(
            image, build_number, provider) + "/"

        if upload_number is None:
            path = self.client.create(
                upload_path,
                value=image_data.serialize(),
                sequence=True,
                makepath=True)
            upload_number = path.split("/")[-1]
        else:
            path = upload_path + upload_number
            self.client.set(path, image_data.serialize())

        return upload_number

    def hasBuildRequest(self, image):
        '''
        Check if an image has a pending build request.

        :param str image: The image name to check.

        :returns: True if request is pending, False otherwise
        '''
        path = self._imageBuildRequestPath(image)
        if self.client.exists(path) is not None:
            return True
        return False

    def submitBuildRequest(self, image):
        '''
        Submit a request for a new image build.

        :param str image: The image name.
        '''
        path = self._imageBuildRequestPath(image)
        self.client.ensure_path(path)

    def removeBuildRequest(self, image):
        '''
        Remove an image build request.

        :param str image: The image name to check.
        '''
        path = self._imageBuildRequestPath(image)
        try:
            self.client.delete(path)
        except kze.NoNodeError:
            pass

    def deleteBuild(self, image, build_number):
        '''
        Delete an image build from ZooKeeper.

        Any provider uploads for this build must be deleted before the build
        node can be deleted.

        :param str image: The image name.
        :param str build_number: The image build number to delete.

        :returns: True if the build is successfully deleted or did not exist,
           False if the provider uploads still exist.
        '''
        path = self._imageBuildsPath(image)
        path = path + "/%s" % build_number

        # Verify that no upload znodes exist.
        for prov in self.getBuildProviders(image, build_number):
            if self.getImageUploadNumbers(image, build_number, prov):
                return False

        try:
            # NOTE: Need to do recursively to remove lock znodes
            self.client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

        return True

    def deleteUpload(self, image, build_number, provider, upload_number):
        '''
        Delete an image upload from ZooKeeper.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param str upload_number: The image upload number to delete.
        '''
        path = self._imageUploadPath(image, build_number, provider)
        path = path + "/%s" % upload_number
        try:
            self.client.delete(path)
        except kze.NoNodeError:
            pass

    def registerLauncher(self, launcher):
        '''
        Register an active node launcher.

        The launcher is automatically de-registered once it terminates or
        otherwise disconnects from ZooKeeper. It will need to re-register
        after a lost connection. This method is safe to call multiple times.

        :param str launcher: Unique name for the launcher.
        '''
        path = self._launcherPath(launcher)

        try:
            self.client.create(path, makepath=True, ephemeral=True)
        except kze.NodeExistsError:
            pass

    def getRegisteredLaunchers(self):
        '''
        Get a list of all launchers that have registered with ZooKeeper.

        :returns: A list of launcher names, or empty list if none are found.
        '''
        try:
            launchers = self.client.get_children(self.LAUNCHER_ROOT)
        except kze.NoNodeError:
            return []

        return launchers

    def getNodeRequests(self):
        '''
        Get the current list of all node requests in priority sorted order.

        :returns: A list of request nodes.
        '''
        try:
            requests = self.client.get_children(self.REQUEST_ROOT)
        except kze.NoNodeError:
            return []

        return sorted(requests)

    def getNodeRequestLockIDs(self):
        '''
        Get the current list of all node request lock ids.
        '''
        try:
            lock_ids = self.client.get_children(self.REQUEST_LOCK_ROOT)
        except kze.NoNodeError:
            return []
        return lock_ids

    def getNodeRequestLockStats(self, lock_id):
        '''
        Get the data for a specific node request lock.

        Note that there is no user data set on a node request lock znode. The
        main purpose for this method is to get the ZK stat data for the lock
        so we can inspect it and use it for lock deletion.

        :param str lock_id: The node request lock ID.

        :returns: A NodeRequestLockStats object.
        '''
        path = self._requestLockPath(lock_id)
        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None
        d = NodeRequestLockStats(lock_id)
        d.stat = stat
        return d

    def deleteNodeRequestLock(self, lock_id):
        '''
        Delete the znode for a node request lock id.

        :param str lock_id: The lock ID.
        '''
        path = self._requestLockPath(lock_id)
        try:
            self.client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def getNodeRequest(self, request):
        '''
        Get the data for a specific node request.

        :param str request: The request ID.

        :returns: The request data, or None if the request was not found.
        '''
        path = self._requestPath(request)
        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None

        d = NodeRequest.fromDict(self._bytesToDict(data), request)
        d.stat = stat
        return d

    def storeNodeRequest(self, request, priority="100"):
        '''
        Store a new or existing node request.

        :param NodeRequest request: The node request to update.
        :param str priority: Priority of a new request. Ignored on updates.
        '''
        if not request.id:
            path = "%s/%s-" % (self.REQUEST_ROOT, priority)
            path = self.client.create(
                path,
                value=request.serialize(),
                ephemeral=True,
                sequence=True,
                makepath=True)
            request.id = path.split("/")[-1]

        # Validate it still exists before updating
        else:
            if not self.getNodeRequest(request.id):
                raise Exception(
                    "Attempt to update non-existing request %s" % request)

            path = self._requestPath(request.id)
            self.client.set(path, request.serialize())

    def deleteNodeRequest(self, request):
        '''
        Delete a node request.

        :param NodeRequest request: The request to delete.
        '''
        if not request.id:
            return

        path = self._requestPath(request.id)
        try:
            self.client.delete(path)
        except kze.NoNodeError:
            pass

    def lockNodeRequest(self, request, blocking=True, timeout=None):
        '''
        Lock a node request.

        This will set the `lock` attribute of the request object when the
        lock is successfully acquired.

        :param NodeRequest request: The request to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        path = self._requestLockPath(request.id)
        try:
            lock = Lock(self.client, path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Request not found for locking: %s", request)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % path)

        request.lock = lock

    def unlockNodeRequest(self, request):
        '''
        Unlock a node request.

        The request must already have been locked.

        :param NodeRequest request: The request to unlock.

        :raises: ZKLockException if the request is not currently locked.
        '''
        if request.lock is None:
            raise npe.ZKLockException(
                "Request %s does not hold a lock" % request)
        request.lock.release()
        request.lock = None

    def lockNode(self, node, blocking=True, timeout=None):
        '''
        Lock a node.

        This will set the `lock` attribute of the Node object when the
        lock is successfully acquired.

        :param Node node: The node to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        path = self._nodeLockPath(node.id)
        try:
            lock = Lock(self.client, path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Node not found for locking: %s", node)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % path)

        node.lock = lock

    def unlockNode(self, node):
        '''
        Unlock a node.

        The node must already have been locked.

        :param Node node: The node to unlock.

        :raises: ZKLockException if the node is not currently locked.
        '''
        if node.lock is None:
            raise npe.ZKLockException("Node %s does not hold a lock" % node)
        node.lock.release()
        node.lock = None

    def getNodes(self):
        '''
        Get the current list of all nodes.

        :returns: A list of nodes.
        '''
        try:
            return self.client.get_children(self.NODE_ROOT)
        except kze.NoNodeError:
            return []

    def getNode(self, node):
        '''
        Get the data for a specific node.

        :param str node: The node ID.

        :returns: The node data, or None if the node was not found.
        '''
        path = self._nodePath(node)
        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None
        if not data:
            return None

        d = Node.fromDict(self._bytesToDict(data), node)
        d.id = node
        d.stat = stat
        return d

    def storeNode(self, node):
        '''
        Store an new or existing node.

        If this is a new node, then node.id will be set with the newly created
        node identifier. Otherwise, node.id is used to identify the node to
        update.

        :param Node node: The Node object to store.
        '''
        if not node.id:
            node_path = "%s/" % self.NODE_ROOT

            # We expect a new node to always have a state already set, so
            # use that state_time for created_time for consistency. But have
            # this check, just in case.
            if node.state_time:
                node.created_time = node.state_time
            else:
                node.created_time = time.time()

            path = self.client.create(
                node_path,
                value=node.serialize(),
                sequence=True,
                makepath=True)
            node.id = path.split("/")[-1]
        else:
            path = self._nodePath(node.id)
            self.client.set(path, node.serialize())

    def deleteNode(self, node):
        '''
        Delete a node.

        :param Node node: The Node object representing the ZK node to delete.
        '''
        if not node.id:
            return

        path = self._nodePath(node.id)
        try:
            self.client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def getReadyNodesOfTypes(self, labels):
        '''
        Query ZooKeeper for unused/ready nodes.

        :param list labels: The node types we want.

        :returns: A dictionary, keyed by node type, with lists of Node objects
            that are ready, or an empty dict if none are found.
        '''
        ret = {}
        for node in self.nodeIterator():
            if (node.state == READY and
                    not node.allocated_to and node.type in labels):
                if node.type not in ret:
                    ret[node.type] = []
                ret[node.type].append(node)
        return ret

    def nodeIterator(self):
        '''
        Utility generator method for iterating through all nodes.
        '''
        for node_id in self.getNodes():
            node = self.getNode(node_id)
            if node:
                yield node

    def nodeRequestLockStatsIterator(self):
        '''
        Utility generator method for iterating through all nodes request locks.
        '''
        for lock_id in self.getNodeRequestLockIDs():
            lock_stats = self.getNodeRequestLockStats(lock_id)
            if lock_stats:
                yield lock_stats

    def nodeRequestIterator(self):
        '''
        Utility generator method for iterating through all nodes requests.
        '''
        for req_id in self.getNodeRequests():
            req = self.getNodeRequest(req_id)
            if req:
                yield req

    def countPoolNodes(self, provider_name, pool_name):
        '''
        Count the number of nodes that exist for the given provider pool.

        :param str provider_name: The provider name.
        :param str pool_name: The pool name.
        '''
        count = 0
        for node in self.nodeIterator():
            if node.provider == provider_name and node.pool == pool_name:
                count = count + 1
        return count
