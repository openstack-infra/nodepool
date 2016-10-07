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
        self._became_lost = False

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

    def _imageBuildLockPath(self, image):
        return "%s/lock" % self._imageBuildsPath(image)

    def _imageProviderPath(self, image, build_number):
        return "%s/%s/provider" % (self._imageBuildsPath(image),
                                   build_number)

    def _imageUploadPath(self, image, build_number, provider):
        return "%s/%s/provider/%s/images" % (self._imageBuildsPath(image),
                                             build_number,
                                             provider)

    def _imageUploadLockPath(self, image, build_number, provider):
        return "%s/lock" % self._imageUploadPath(image, build_number,
                                                 provider)

    def _dictToStr(self, data):
        return json.dumps(data)

    def _strToDict(self, data):
        return json.loads(data)

    def _getImageBuildLock(self, image, blocking=True, timeout=None):
        lock = self._imageBuildLockPath(image)
        try:
            self._current_lock = Lock(self.client, lock)
            have_lock = self._current_lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock)

    def _getImageUploadLock(self, image, build_number, provider,
                            blocking=True, timeout=None):
        lock = self._imageUploadLockPath(image, build_number, provider)
        try:
            self._current_lock = Lock(self.client, lock)
            have_lock = self._current_lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock)

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
    # Public Methods and Properties
    #========================================================================

    @property
    def connected(self):
        return self.client.state == KazooState.CONNECTED

    @property
    def suspended(self):
        return self.client.state == KazooState.SUSPENDED

    @property
    def lost(self):
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

    @contextmanager
    def imageBuildLock(self, image, blocking=True, timeout=None):
        '''
        Context manager to use for locking image builds.

        Obtains a write lock for the specified image. A thread of control
        using this API may have only one image locked at a time.

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
            yield self._getImageBuildLock(image, blocking, timeout)
        finally:
            if self._current_lock:
                self._current_lock.release()
                self._current_lock = None

    @contextmanager
    def imageUploadLock(self, image, build_number, provider,
                        blocking=True, timeout=None):
        '''
        Context manager to use for locking image builds.

        Obtains a write lock for the specified image upload. A thread of
        control using this API may have only one lock at a time.

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
        if self._current_lock:
            raise npe.ZKLockException("A lock is already held.")

        try:
            yield self._getImageUploadLock(image, build_number, provider,
                                           blocking, timeout)
        finally:
            if self._current_lock:
                self._current_lock.release()
                self._current_lock = None

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
        return images

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

        return providers

    def getImageUploadNumbers(self, image, build_number, provider):
        '''
        Retrieve upload numbers for a provider and image build.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.

        :returns: A list of upload numbers or the empty list.

        '''
        path = self._imageUploadPath(image, build_number, provider)
        print path

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

        :returns: The dictionary of build data, or None if not found.
        '''
        path = self._imageBuildsPath(image) + "/%s" % build_number

        if not self.client.exists(path):
            return None

        data, stat = self.client.get(path)
        return self._strToDict(data)

    def getBuildsWithStates(self, image, states):
        '''
        Retrieve all image build data matching the given states.

        :param str image: The image name.
        :param list states: A list of build state values to match against.
            An empty string ('') will match states with no state recorded.

        :returns: A dictionary of dictionaries of build data, keyed by
            build number, or None if not found.
        '''
        path = self._imageBuildsPath(image)

        if not self.client.exists(path):
            return None

        matches = {}
        builds = self.client.get_children(path)
        if not builds:
            return None

        for build in builds:
            if build == 'lock':   # skip the build lock node
                continue
            data = self.getBuild(image, build)
            if not data or data.get('state', '') in states:
                matches[build] = data

        if not matches:
            return None
        return matches

    def getMostRecentBuild(self, image, state="ready"):
        '''
        Retrieve the most recent image build data with the given state.

        :param str image: The image name.
        :param str state: The build state to match on.

        :returns: A tuple with the most recent build number and dictionary of
            build data matching the given state, or None if there was no build
            matching the state.
        '''
        recent_data = None
        recent_bnum = None
        for build in self.getBuildNumbers(image):
            data = self.getBuild(image, build)
            if data.get('state', '') != state:
                continue
            elif (recent_data is None or
                  recent_data['state_time'] < data.get('state_time', 0)
            ):
                recent_bnum = build
                recent_data = data

        if recent_bnum is None and recent_data is None:
            return None

        return (recent_bnum, recent_data)

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
        :param dict build_data: The build data.
        :param str build_number: The image build number.

        :returns: A string for the build number that was updated.
        '''
        # Append trailing / so the sequence node is created as a child node.
        build_path = self._imageBuildsPath(image) + "/"

        if build_number is None:
            path = self.client.create(build_path,
                                      value=self._dictToStr(build_data),
                                      sequence=True,
                                      makepath=True)
            build_number = path.split("/")[-1]
        else:
            path = build_path + build_number
            self.client.set(path, self._dictToStr(build_data))

        return build_number

    def getImageUpload(self, image, build_number, provider, upload_number):
        '''
        Retrieve the image upload data.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param str upload_number: The image upload number.

        :returns: A dict of upload data.

        :raises: ZKException if the image upload path is not found.
        '''
        path = self._imageUploadPath(image, build_number, provider)
        path = path + "/%s" % upload_number

        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            raise npe.ZKException(
                "Cannot find upload data "
                "(image: %s, build: %s, provider: %s, upload: %s)" % (
                    image, build_number, provider, upload_number)
            )

        return self._strToDict(data)

    def getMostRecentImageUpload(self, image, build_number, provider,
                                 state="ready"):
        '''
        Retrieve the most recent image upload data with the given state.

        :param str image: The image name.
        :param str build_number: The image build number.
        :param str provider: The provider name owning the image.
        :param str state: The image upload state to match on.

        :returns: A tuple with the most recent upload number and dictionary of
            upload data matching the given state, or None if there was no
            upload matching the state.
        '''
        path = self._imageUploadPath(image, build_number, provider)

        if not self.client.exists(path):
            return None

        uploads = self.client.get_children(path)
        if not uploads:
            return None

        recent_upnum = None
        recent_data = None
        for upload in uploads:
            if upload == 'lock':   # skip the upload lock node
                continue
            data = self.getImageUpload(image, build_number, provider, upload)
            if data.get('state', '') != state:
                continue
            elif (recent_data is None or
                  recent_data['state_time'] < data.get('state_time', 0)
            ):
                recent_upnum = upload
                recent_data = data

        if recent_upnum is None and recent_data is None:
            return None

        return (recent_upnum, recent_data)

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
        :param dict image_data: The image data we want to store.
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
        upload_path = self._imageUploadPath(image, build_number, provider) + "/"

        if upload_number is None:
            path = self.client.create(upload_path,
                                      value=self._dictToStr(image_data),
                                      sequence=True,
                                      makepath=True)
            upload_number = path.split("/")[-1]
        else:
            path = upload_path + upload_number
            self.client.set(path, self._dictToStr(image_data))

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

