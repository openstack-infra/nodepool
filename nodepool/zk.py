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
import json
import logging
from kazoo.client import KazooClient, KazooState
from kazoo import exceptions as kze
from kazoo.recipe.lock import Lock

from nodepool import exceptions as npe


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

    If you will have multiple threads needing this API, each thread should
    instantiate their own ZooKeeper object. It should not be shared.
    '''

    log = logging.getLogger("nodepool.zk.ZooKeeper")

    IMAGE_ROOT = "/nodepool/image"

    def __init__(self, client=None):
        '''
        Initialize the ZooKeeper object.

        :param client: A pre-connected client. Optionally, you may choose
            to use the connect() call.
        '''
        self.client = client
        self._current_lock = None

        # Dictionary that maps an image build request path being watched to
        # the function to call when triggered. Why have this? We may need to
        # handle automatically re-registering these watches for the user in
        # the event of a disconnect from the cluster.
        # TODO(Shrews): Hande re-registration
        self._data_watches = {}

    #========================================================================
    # Private Methods
    #========================================================================

    def _imagePath(self, image):
        return "%s/%s" % (self.IMAGE_ROOT, image)

    def _imageBuildRequestPath(self, image):
        return "%s/request-build" % self._imagePath(image)

    def _imageBuildsPath(self, image):
        return "%s/builds" % self._imagePath(image)

    def _imageLockPath(self, image):
        return "%s/lock" % self._imageBuildsPath(image)

    def _imageUploadPath(self, image, build_number, provider):
        return "%s/%s/provider/%s/images" % (self._imageBuildsPath(image),
                                             build_number,
                                             provider)
    def _dictToStr(self, data):
        return json.dumps(data)

    def _strToDict(self, data):
        return json.loads(data)

    def _getImageLock(self, image, blocking=True, timeout=None):
        # If we don't already have a znode for this image, create it.
        image_lock = self._imageLockPath(image)
        try:
            self.client.ensure_path(self._imagePath(image))
            self._current_lock = Lock(self.client, image_lock)
            have_lock = self._current_lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % image_lock)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % image_lock)

    def _getImageBuildLock(self, image, blocking=True, timeout=None):
        '''
        This differs from _get_image_lock() in that it creates a new build
        znode and returns its name to the caller.
        '''
        self._getImageLock(image, blocking, timeout)

        # Create new znode with new build_number
        build_number = self.getMaxBuildId(image) + 1
        self.client.create(
            self._imageBuildsPath(image) + "/%s" % build_number
        )

        return build_number

    def _connection_listener(self, state):
        '''
        Listener method for Kazoo connection state changes.

        .. warning:: This method must not block.
        '''
        if state == KazooState.LOST:
            self.log.debug("ZooKeeper connection: LOST")
        elif state == KazooState.SUSPENDED:
            self.log.debug("ZooKeeper connection: SUSPENDED")
        else:
            self.log.debug("ZooKeeper connection: CONNECTED")

    def _watch_wrapper(self, event):
        '''
        Function used to handle watch triggers.

        This handles unregistering watch events from the internal mapping
        and calling the registered function as requested during registration.
        '''
        if event.path not in self._data_watches:
            self.log.error(
                "Got trigger on %s but watch not registered" % event.path)
            return
        (image, func) = self._data_watches[event.path]
        del self._data_watches[event.path]
        func(ZooKeeperWatchEvent(event.type, event.state, event.path, image))


    #========================================================================
    # Public Methods
    #========================================================================

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
        if self.client is not None:
            self.client.stop()
            self.client = None

    def getMaxBuildId(self, image):
        '''
        Find the highest build number for a given image.

        Image builds are integer znodes, which are children of the 'builds'
        parent znode.

        :param str image: The image name.

        :returns: An int value for the max existing image build number, or
            zero if none exist.

        :raises: ZKException if the image build path is not found.
        '''
        path = self._imageBuildsPath(image)

        if not self.client.exists(path):
            raise npe.ZKException(
                "Image build path not found for image %s" % image
            )

        max_found = 0
        children = self.client.get_children(path)
        if children:
            for child in children:
                # There can be a lock znode that we should ignore
                if child != 'lock':
                    max_found = max(max_found, int(child))
        return max_found

    def getMaxImageUploadId(self, image, build_number, provider):
        '''
        Find the highest image upload number for a given image for a provider.

        For a given image build, it may have been uploaded one or more times
        to a provider (with once being the most common case). Each upload is
        given its own znode, which is a integer increased by one for each
        upload. This method gets the highest numbered znode.

        :param str image: The image name.
        :param int build_number: The image build number.
        :param str provider: The provider name owning the image.

        :returns: An int value for the max existing image upload number, or
            zero if none exist.

        :raises: ZKException if the image upload path is not found.
        '''
        path = self._imageUploadPath(image, build_number, provider)

        if not self.client.exists(path):
            raise npe.ZKException(
                "Image upload path not found for build %s of image %s" % (
                    build_number, provider)
            )

        max_found = 0
        children = self.client.get_children(path )
        if children:
            max_found = max([int(child) for child in children])
        return max_found

    @contextmanager
    def imageLock(self, image, blocking=True, timeout=None):
        '''
        Context manager to use for locking an image.

        Obtains a write lock for the specified image. A thread of control
        using this API may have only one image locked at a time. This is
        different from image_build_lock() in that a new build node is NOT
        created and returned.

        :param str image: Name of the image to lock
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        if self._current_lock:
            raise npe.ZKLockException("A lock is already held.")

        try:
            yield self._getImageLock(image, blocking, timeout)
        finally:
            if self._current_lock:
                self._current_lock.release()
                self._current_lock = None

    @contextmanager
    def imageBuildLock(self, image, blocking=True, timeout=None):
        '''
        Context manager to use for locking new image builds.

        Obtains a write lock for the specified image. A thread of control
        using this API may have only one image locked at a time. A new
        znode is created with the next highest build number. This build
        number is returned to the caller.

        :param str image: Name of the image to lock
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :returns: A integer to use for the new build id.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        if self._current_lock:
            raise npe.ZKLockException("A lock is already held.")

        try:
            yield self._getImageBuildLock(image, blocking, timeout)
        finally:
            if self._current_lock:
                self._current_lock.release()
                self._current_lock = None

    def getBuild(self, image, build_number):
        '''
        Retrieve the image build data.

        :param str image: The image name.
        :param int build_number: The image build number.

        :returns: The dictionary of build data.
        '''
        path = self._imageBuildsPath(image) + "/%s" % build_number

        if not self.client.exists(path):
            raise npe.ZKException(
                "Cannot find build data (image: %s, build: %s)" % (
                    image, build_number)
            )

        data, stat = self.client.get(path)
        return self._strToDict(data)

    def storeBuild(self, image, build_number, build_data):
        '''
        Store the image build data.

        The build data is either created if it does not exist, or it is
        updated in its entirety if it does not. There is no partial updating.
        The build data is expected to be represented as a dict. This dict may
        contain any data, as appropriate.

        :param str image: The image name for which we have data.
        :param int build_number: The image build number.
        :param dict build_data: The build data.

        :raises: ZKException if the build znode does not exist (it is created
            with the image_build_lock() context manager).
        '''
        path = self._imageBuildsPath(image) + "/%s" % build_number

        # The build path won't exist until it's created with the build lock
        if not self.client.exists(path):
            raise npe.ZKException(
                "%s does not exist. Did you lock it?" % path)

        self.client.set(path, self._dictToStr(build_data))

    def getImageUpload(self, image, build_number, provider,
                         upload_number=None):
        '''
        Retrieve the image upload data.

        :param str image: The image name.
        :param int build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param int build_number: The image upload number. If this is None,
            the most recent upload data is returned.

        :returns: A dict of upload data.

        :raises: ZKException if the image upload path is not found.
        '''
        if upload_number is None:
            upload_number = self.getMaxImageUploadId(image, build_number,
                                                     provider)

        path = self._imageUploadPath(image, build_number, provider)
        path = path + "/%s" % upload_number

        if not self.client.exists(path):
            raise npe.ZKException(
                "Cannot find upload data "
                "(image: %s, build: %s, provider: %s, upload: %s)" % (
                    image, build_number, provider, upload_number)
            )

        data, stat = self.client.get(path)
        return self._strToDict(data)

    def storeImageUpload(self, image, build_number, provider, image_data):
        '''
        Store the built image's upload data for the given provider.

        :param str image: The image name for which we have data.
        :param int build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param dict image_data: The image data we want to store.

        :returns: An int for the new upload id.

        :raises: ZKException for an invalid image build.
        '''
        # We expect the image builds path to already exist.
        build_path = self._imageBuildsPath(image)
        if not self.client.exists(build_path):
            raise npe.ZKException(
                "Cannot find build %s of image %s" % (build_number, provider)
            )

        # Generate a path for the upload. This doesn't have to exist yet
        # since we'll create new provider/upload ID znodes automatically.
        path = self._imageUploadPath(image, build_number, provider)

        # We need to create the provider upload path if it doesn't exist
        # before we attempt to get the max image upload ID next.
        self.client.ensure_path(path)

        # Get a new upload ID
        next_id = self.getMaxImageUploadId(image, build_number, provider) + 1

        path = path + "/%s" % next_id
        self.client.create(path, self._dictToStr(image_data))

        return next_id

    def registerBuildRequestWatch(self, image, func):
        '''
        Register a watch for a node create/delete or data change.

        This registers a one-time watch trigger for a build request for
        a particular image. Your handler function will need to re-register
        after an event is received if you want to continue watching.

        Your handler will receive an object of ZooKeeperWatchEvent type.

        :param str image: The image name we want to watch.
        :param func: A function to call when the watch is triggered.
        '''
        path = self._imageBuildRequestPath(image)
        self._data_watches[path] = (image, func)
        self.client.exists(path, watch=self._watch_wrapper)
