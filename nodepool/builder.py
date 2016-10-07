#!/usr/bin/env python
# Copyright 2015 Hewlett-Packard Development Company, L.P.
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

import logging
import os
import socket
import subprocess
import threading
import time

import shlex

import config as nodepool_config
import exceptions
import provider_manager
import stats
import zk


MINS = 60
HOURS = 60 * MINS
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save
SUSPEND_WAIT_TIME = 30       # How long to wait between checks for
                             # ZooKeeper connectivity if it disappears.

# HP Cloud requires qemu compat with 0.10. That version works elsewhere,
# so just hardcode it for all qcow2 building
DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS = "--qemu-img-options 'compat=0.10'"


class DibImageFile(object):
    '''
    Class used as an API to finding locally built DIB image files, and
    also used to represent the found files. Image files are named using
    a unique ID, but can be available in multiple formats (with different
    extensions).
    '''
    def __init__(self, image_id, extension=None):
        self.image_id = image_id
        self.extension = extension

    @staticmethod
    def from_path(path):
        image_file = os.path.basename(path)
        image_id, extension = image_file.rsplit('.', 1)
        return DibImageFile(image_id, extension)

    @staticmethod
    def from_image_id(images_dir, image_id):
        images = []
        for image_filename in os.listdir(images_dir):
            if os.path.isfile(os.path.join(images_dir, image_filename)):
                image = DibImageFile.from_path(image_filename)
                if image.image_id == image_id:
                        images.append(image)
        return images

    @staticmethod
    def from_images_dir(images_dir):
        return [DibImageFile.from_path(x) for x in os.listdir(images_dir)]

    def to_path(self, images_dir, with_extension=True):
        my_path = os.path.join(images_dir, self.image_id)
        if with_extension:
            if self.extension is None:
                raise exceptions.BuilderError(
                    'Cannot specify image extension of None'
                )
            my_path += '.' + self.extension
        return my_path


class BaseWorker(threading.Thread):
    log = logging.getLogger("nodepool.builder.BaseWorker")

    def __init__(self, config_path):
        super(BaseWorker, self).__init__()
        self.daemon = True
        self._running = False
        self._config = None
        self._config_path = config_path
        self._zk = None
        self._hostname = socket.gethostname()
        self._statsd = stats.get_client()

    def _checkForZooKeeperChanges(self, new_config):
        '''
        Connect to ZooKeeper cluster.

        Makes the initial connection to the ZooKeeper cluster. If the defined
        set of ZooKeeper servers changes, the connection will be reestablished
        using the new server set.
        '''
        if self._zk is None:
            self.log.debug("Connecting to ZooKeeper servers")
            self._zk = zk.ZooKeeper()
            self._zk.connect(new_config.zookeeper_servers.values())
        elif self._config.zookeeper_servers != new_config.zookeeper_servers:
            self.log.debug("Detected ZooKeeper server changes")
            self._zk.disconnect()
            self._zk.connect(new_config.zookeeper_servers.values())

    @property
    def running(self):
        return self._running

    def shutdown(self):
        self._running = False


class BuildWorker(BaseWorker):
    log = logging.getLogger("nodepool.builder.BuildWorker")

    def __init__(self, config_path):
        super(BuildWorker, self).__init__(config_path)

    def _makeStateData(self, state):
        '''
        Create a build state dict with minimal, common state information.

        Sets common attributes, such as builder host, state, and state time.

        :param str state: The build state you want.
        '''
        data = {}
        data['builder'] = self._hostname
        data['state'] = state
        data['state_time'] = int(time.time())
        return data

    def _checkForScheduledImageUpdates(self):
        '''
        Check every DIB image to see if it has aged out and needs rebuilt.

        .. note:: It's important to lock the image build before we check
            the state time and then build to eliminate any race condition.
        '''
        for name, image in self._config.diskimages.items():
            # Check if we've been told to shutdown
            # or if ZK connection is suspended
            if not self.running or self._zk.suspended or self._zk.lost:
                return

            now = int(time.time())
            builds = self._zk.getMostRecentBuilds(1, name, 'ready')

            # If there is no build for this image, or it has aged out
            # or if the current build is missing an image type from
            # the config file, start a new build.
            if (not builds
                or (now - builds[0][1]['state_time']) >= image.rebuild_age
                or not set(builds[0][1]['formats'].split(',')).\
                    issuperset(image.image_types)
            ):
                try:
                    with self._zk.imageBuildLock(image.name, blocking=False):
                        # To avoid locking each image repeatedly, we have an
                        # second, redundant check here to verify that a new
                        # build didn't appear between the first check and the
                        # lock acquisition. If it's not the same build as
                        # identified in the first check above, assume another
                        # BuildWorker created the build for us and continue.
                        builds2 = self._zk.getMostRecentBuilds(1, name, 'ready')
                        if builds2 and builds[0][0] != builds2[0][0]:
                            continue

                        self.log.info("Building image %s" % name)
                        bnum = self._zk.storeBuild(
                            image.name, self._makeStateData('building'))
                        data = self._buildImage(bnum, image)
                        self._zk.storeBuild(image.name, data, bnum)
                except exceptions.ZKLockException:
                    # Lock is already held. Skip it.
                    pass

    def _checkForManualBuildRequest(self):
        '''
        Query ZooKeeper for any manual image build requests.
        '''
        for image in self._config.diskimages.values():
            # Check if we've been told to shutdown
            # or if ZK connection is suspended
            if not self.running or self._zk.suspended or self._zk.lost:
                return

            # Reduce use of locks by adding an initial check here and
            # a redundant check after lock acquisition.
            if not self._zk.hasBuildRequest(image.name):
                continue

            try:
                with self._zk.imageBuildLock(image.name, blocking=False):
                    # Redundant check
                    if not self._zk.hasBuildRequest(image.name):
                        continue

                    self.log.info(
                        "Manual build request for image %s" % image.name)

                    bnum = self._zk.storeBuild(
                        image.name, self._makeStateData('building'))
                    data = self._buildImage(bnum, image)
                    self._zk.storeBuild(image.name, data, bnum)

                    # Remove request on a successful build
                    if data['state'] == 'ready':
                        self._zk.removeBuildRequest(image.name)

            except exceptions.ZKLockException:
                # Lock is already held. Skip it.
                pass

    def _buildImage(self, build_id, image):
        '''
        Run the external command to build the image.

        :param str build_id: The ID for the build (used in image filename).
        :param image: The image as retrieved from our config file.

        :returns: A dict of build-related data.

        :raises: BuilderError if we failed to execute the build command.
        '''
        image_file = DibImageFile(build_id)
        filename = image_file.to_path(self._config.imagesdir, False)

        env = os.environ.copy()
        env['DIB_RELEASE'] = image.release
        env['DIB_IMAGE_NAME'] = image.name
        env['DIB_IMAGE_FILENAME'] = filename

        # Note we use a reference to the nodepool config here so
        # that whenever the config is updated we get up to date
        # values in this thread.
        if self._config.elementsdir:
            env['ELEMENTS_PATH'] = self._config.elementsdir
        if self._config.scriptdir:
            env['NODEPOOL_SCRIPTDIR'] = self._config.scriptdir

        # this puts a disk-usage report in the logs so we can see if
        # something blows up the image size.
        env['DIB_SHOW_IMAGE_USAGE'] = '1'

        # send additional env vars if needed
        for k, v in image.env_vars.items():
            env[k] = v

        img_elements = image.elements
        img_types = ",".join(image.image_types)

        qemu_img_options = ''
        if 'qcow2' in img_types:
            qemu_img_options = DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS

        if 'fake-' in image.name:
            dib_cmd = 'nodepool/tests/fake-image-create'
        else:
            dib_cmd = 'disk-image-create'

        cmd = ('%s -x -t %s --no-tmpfs %s -o %s %s' %
               (dib_cmd, img_types, qemu_img_options, filename, img_elements))

        log = logging.getLogger("nodepool.image.build.%s" %
                                (image.name,))

        self.log.info('Running %s' % cmd)

        try:
            p = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env)
        except OSError as e:
            raise exceptions.BuilderError(
                "Failed to exec '%s'. Error: '%s'" % (cmd, e.strerror)
            )

        while True:
            ln = p.stdout.readline()
            log.info(ln.strip())
            if not ln:
                break

        p.wait()

        # It's possible the connection to the ZK cluster could have been
        # interrupted during the build. If so, wait for it to return.
        # It could transition directly from SUSPENDED to CONNECTED, or go
        # through the LOST state before CONNECTED.
        while self._zk.suspended or self._zk.lost:
            self.log.info("ZooKeeper suspended during build. Waiting")
            time.sleep(SUSPEND_WAIT_TIME)

        if self._zk.didLoseConnection:
            self.log.info("ZooKeeper lost while building %s" % image.name)
            self._zk.resetLostFlag()
            build_data = self._makeStateData('failed')
        elif p.returncode:
            self.log.info("DIB failed creating %s" % image.name)
            build_data = self._makeStateData('failed')
        else:
            self.log.info("DIB image %s is built" % image.name)
            build_data = self._makeStateData('ready')
            build_data['formats'] = img_types

            if self._statsd:
                # record stats on the size of each image we create
                for ext in img_types.split(','):
                    key = 'nodepool.dib_image_build.%s.%s.size' % (image.name, ext)
                    # A bit tricky because these image files may be sparse
                    # files; we only want the true size of the file for
                    # purposes of watching if we've added too much stuff
                    # into the image.  Note that st_blocks is defined as
                    # 512-byte blocks by stat(2)
                    size = os.stat("%s.%s" % (filename, ext)).st_blocks * 512
                    self.log.debug("%s created image %s.%s (size: %d) " %
                                   (image.name, filename, ext, size))
                    self._statsd.gauge(key, size)

        return build_data

    def run(self):
        '''
        Start point for the BuildWorker thread.
        '''
        self._running = True
        while self._running:
            # Don't do work if we've lost communication with the ZK cluster
            while self._zk and (self._zk.suspended or self._zk.lost):
                self.log.info("ZooKeeper suspended. Waiting")
                time.sleep(SUSPEND_WAIT_TIME)

            # NOTE: For the first iteration, we expect self._config to be None
            new_config = nodepool_config.loadConfig(self._config_path)
            self._checkForZooKeeperChanges(new_config)
            self._config = new_config

            self._checkForScheduledImageUpdates()
            self._checkForManualBuildRequest()

            # TODO: Make this configurable
            time.sleep(0.1)

        if self._zk:
            self._zk.disconnect()


class UploadWorker(BaseWorker):
    log = logging.getLogger("nodepool.builder.UploadWorker")

    def __init__(self, config_path):
        super(UploadWorker, self).__init__(config_path)

    def _makeStateData(self, state):
        '''
        Create an upload state dict with minimal, common state information.

        :param str state: The upload state you want.
        '''
        data = {}
        data['state'] = state
        data['state_time'] = int(time.time())
        return data

    def _uploadImage(self, build_id, image_name, images, provider):
        '''
        Upload a local DIB image build to a provider.

        :param str build_id: Unique ID of the image build to upload.
        :param str image_name: Name of the diskimage.
        :param list images: A list of DibImageFile objects from this build
            that available for uploading.
        :param provider: The provider from the parsed config file.
        '''
        start_time = time.time()
        timestamp = int(start_time)

        image = None
        for i in images:
            if provider.image_type == i.extension:
                image = i
                break

        if not image:
            raise exceptions.BuilderInvalidCommandError(
                "Unable to find image file of type %s for id %s to upload" %
                (provider.image_type, build_id)
            )

        self.log.debug("Found image file of type %s for image id: %s" %
                       (image.extension, image.image_id))

        filename = image.to_path(self._config.imagesdir, with_extension=True)

        dummy_image = type('obj', (object,), {'name': image_name})

        ext_image_name = provider.template_hostname.format(
            provider=provider, image=dummy_image,
            timestamp=str(timestamp)
        )

        self.log.info("Uploading DIB image build %s from %s to %s" %
                      (build_id, filename, provider.name))

        manager = self._config.provider_managers[provider.name]
        try:
            provider_image = provider.images[image_name]
        except KeyError:
            raise exceptions.BuilderInvalidCommandError(
                "Could not find matching provider image for %s", image_name
            )

        external_id = manager.uploadImage(
            ext_image_name, filename,
            image_type=image.extension,
            meta=provider_image.meta
        )

        if self._statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (image_name,
                                                   provider.name)
            self._statsd.timing(key, dt)
            self._statsd.incr(key)

        self.log.info("Image build %s in %s is ready" %
                      (build_id, provider.name))

        data = self._makeStateData('ready')
        data['external_id'] = external_id
        data['external_name'] = ext_image_name
        return data

    def _checkForProviderUploads(self):
        '''
        Check for any image builds that need to be uploaded to providers.

        If we find any builds in the 'ready' state that haven't been uploaded
        to providers, do the upload if they are available on the local disk.
        '''
        for provider in self._config.providers.values():
            for image in provider.images.values():
                # Check if we've been told to shutdown
                # or if ZK connection is suspended
                if not self.running or self._zk.suspended or self._zk.lost:
                    return

                if image.name not in self._config.images_in_use:
                    continue
                if not image.diskimage:
                    continue

                # Search for the most recent 'ready' image build
                builds = self._zk.getMostRecentBuilds(1, image.diskimage)
                if not builds:
                    continue

                build_id, build_data = builds[0]

                # Search for locally built images. The build sequence ID is
                # used to name the image.
                local_images = DibImageFile.from_image_id(self._config.imagesdir,
                                                          build_id)
                if not local_images:
                    continue

                # See if this image has already been uploaded
                upload = self._zk.getMostRecentImageUpload(
                    image.diskimage, build_id, provider.name)
                if upload is not None:
                    continue

                # See if this provider supports the available image formats
                image_formats = build_data['formats'].split(',')
                if provider.image_type not in image_formats:
                    continue

                try:
                    with self._zk.imageUploadLock(
                        image.diskimage, build_id, provider.name,
                        blocking=False
                    ):
                        # New upload number with initial state 'uploading'
                        upnum = self._zk.storeImageUpload(
                            image.diskimage, build_id, provider.name,
                            self._makeStateData('uploading'))

                        data = self._uploadImage(build_id, image.diskimage,
                                                 local_images, provider)

                        # Set final state
                        self._zk.storeImageUpload(image.diskimage, build_id,
                                                  provider.name, data, upnum)
                except exceptions.ZKLockException:
                    # Lock is already held. Skip it.
                    pass

    def run(self):
        '''
        Start point for the UploadWorker thread.
        '''
        self._running = True
        while self._running:
            # Don't do work if we've lost communication with the ZK cluster
            while self._zk and (self._zk.suspended or self._zk.lost):
                self.log.info("ZooKeeper suspended. Waiting")
                time.sleep(SUSPEND_WAIT_TIME)

            new_config = nodepool_config.loadConfig(self._config_path)
            self._checkForZooKeeperChanges(new_config)
            provider_manager.ProviderManager.reconfigure(self._config, new_config)
            self._config = new_config

            self._checkForProviderUploads()

            # TODO: Make this configurable
            time.sleep(0.1)

        if self._zk:
            self._zk.disconnect()

        provider_manager.ProviderManager.stopProviders(self._config)

class NodePoolBuilder(object):
    '''
    Main class for the Nodepool Builder.

    The builder has the responsibility to:

        * Handle the image updating process as scheduled via the image-update
          cron job defined within the `cron` config section.
        * Register to receive all watch events from ZooKeeper and assign
          workers for those events.
        * Start and maintain the working state of each worker thread.
    '''
    log = logging.getLogger("nodepool.builder.NodePoolBuilder")

    def __init__(self, config_path, num_builders=1, num_uploaders=4):
        '''
        Initialize the NodePoolBuilder object.

        :param str config_path: Path to configuration file.
        :param int num_builders: Number of build workers to start.
        :param int num_uploaders: Number of upload workers to start.
        '''
        self._config_path = config_path
        self._config = None
        self._num_builders = num_builders
        self._build_workers = []
        self._num_uploaders = num_uploaders
        self._upload_workers = []
        self._running = False

        # This lock is needed because the run() method is started in a
        # separate thread of control, which can return before the scheduler
        # has completed startup. We need to avoid shutting down before the
        # startup process has completed.
        self._start_lock = threading.Lock()

    #=======================================================================
    # Private methods
    #=======================================================================

    def _getAndValidateConfig(self):
        config = nodepool_config.loadConfig(self._config_path)
        if not config.zookeeper_servers.values():
            raise RuntimeError('No ZooKeeper servers specified in config.')
        if not config.imagesdir:
            raise RuntimeError('No images-dir specified in config.')
        return config

    #=======================================================================
    # Public methods
    #=======================================================================

    def start(self):
        '''
        Start the builder.

        The builder functionality is encapsulated within threads run
        by the NodePoolBuilder. This starts the needed sub-threads
        which will run forever until we tell them to stop.
        '''
        with self._start_lock:
            if self._running:
                raise exceptions.BuilderError('Cannot start, already running.')

            self._config = self._getAndValidateConfig()
            self._running = True

            self.log.debug('Starting listener for build jobs')

            # Create build and upload worker objects
            for i in range(self._num_builders):
                w = BuildWorker(self._config_path)
                w.start()
                self._build_workers.append(w)

            for i in range(self._num_uploaders):
                w = UploadWorker(self._config_path)
                w.start()
                self._upload_workers.append(w)

            # Wait until all threads are running. Otherwise, we have a race
            # on the worker _running attribute if shutdown() is called before
            # run() actually begins.
            while not all([
                x.running for x in (self._build_workers + self._upload_workers)
            ]):
                time.sleep(0)

    def stop(self):
        '''
        Stop the builder.

        Signal the sub threads to begin the shutdown process. We don't
        want this method to return until the scheduler has successfully
        stopped all of its own threads.
        '''
        with self._start_lock:
            self.log.debug("Stopping. NodePoolBuilder shutting down workers")
            for worker in (self._build_workers + self._upload_workers):
                worker.shutdown()

        self._running = False

        self.log.debug('Waiting for jobs to complete')

        # Do not exit until all of our owned threads exit.
        for worker in (self._build_workers + self._upload_workers):
            worker.join()

        self.log.debug('Stopping providers')
        provider_manager.ProviderManager.stopProviders(self._config)
        self.log.debug('Finished stopping')


class OldNodePoolBuilder(object):
    '''
    Methods used by the builder that have not moved to the new
    system yet.
    '''
    log = logging.getLogger("nodepool.builder.NodePoolBuilder")

    def __init__(self):
        self._built_image_ids = set()
        self._config = None
        self.statsd = stats.get_client()

    def canHandleImageIdJob(self, job, image_op):
        return (job.name.startswith(image_op + ':') and
                job.name.split(':')[1]) in self._built_image_ids

    def deleteImage(self, image_id):
        image_files = DibImageFile.from_image_id(self._config.imagesdir,
                                                 image_id)

        # Delete a dib image and its associated file
        for image_file in image_files:
            img_path = image_file.to_path(self._config.imagesdir)
            if os.path.exists(img_path):
                self.log.debug('Removing filename %s', img_path)
                os.remove(img_path)
            else:
                self.log.debug('No filename %s found to remove', img_path)
        self.log.info("Deleted dib image id: %s" % image_id)

    def uploadImage(self, image_id, provider_name, image_name):
        start_time = time.time()
        timestamp = int(start_time)

        provider = self._config.providers[provider_name]
        image_type = provider.image_type

        image_files = DibImageFile.from_image_id(self._config.imagesdir,
                                                 image_id)
        for f in image_files:
            self.log.debug("Found image file of type %s for image id: %s" %
                           (f.extension, f.image_id))
        image_files = filter(lambda x: x.extension == image_type, image_files)
        if len(image_files) == 0:
            raise exceptions.BuilderInvalidCommandError(
                "Unable to find image file of type %s for id %s to upload" %
                (image_type, image_id)
            )
        if len(image_files) > 1:
            raise exceptions.BuilderError(
                "Found more than one image for id %s. This should never "
                "happen.", image_id
            )

        image_file = image_files[0]
        filename = image_file.to_path(self._config.imagesdir,
                                      with_extension=True)

        dummy_image = type('obj', (object,),
                           {'name': image_name})
        ext_image_name = provider.template_hostname.format(
            provider=provider, image=dummy_image, timestamp=str(timestamp))
        self.log.info("Uploading dib image id: %s from %s in %s" %
                      (image_id, filename, provider.name))

        manager = self._config.provider_managers[provider.name]
        try:
            provider_image = provider.images[image_name]
        except KeyError:
            raise exceptions.BuilderInvalidCommandError(
                "Could not find matching provider image for %s", image_name
            )
        image_meta = provider_image.meta
        # uploadImage is synchronous
        external_id = manager.uploadImage(
            ext_image_name, filename,
            image_type=image_file.extension,
            meta=image_meta)

        if self.statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (image_name,
                                                   provider.name)
            self.statsd.timing(key, dt)
            self.statsd.incr(key)

        self.log.info("Image id: %s in %s is ready" % (image_id,
                                                       provider.name))
        return external_id

    def _runDibForImage(self, image, filename):
        env = os.environ.copy()

        env['DIB_RELEASE'] = image.release
        env['DIB_IMAGE_NAME'] = image.name
        env['DIB_IMAGE_FILENAME'] = filename
        # Note we use a reference to the nodepool config here so
        # that whenever the config is updated we get up to date
        # values in this thread.
        if self._config.elementsdir:
            env['ELEMENTS_PATH'] = self._config.elementsdir
        if self._config.scriptdir:
            env['NODEPOOL_SCRIPTDIR'] = self._config.scriptdir

        # this puts a disk-usage report in the logs so we can see if
        # something blows up the image size.
        env['DIB_SHOW_IMAGE_USAGE'] = '1'

        # send additional env vars if needed
        for k, v in image.env_vars.items():
            env[k] = v

        img_elements = image.elements
        img_types = ",".join(image.image_types)

        qemu_img_options = ''
        if 'qcow2' in img_types:
            qemu_img_options = DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS

        if 'fake-' in image.name:
            dib_cmd = 'nodepool/tests/fake-image-create'
        else:
            dib_cmd = 'disk-image-create'

        cmd = ('%s -x -t %s --no-tmpfs %s -o %s %s' %
               (dib_cmd, img_types, qemu_img_options, filename, img_elements))

        log = logging.getLogger("nodepool.image.build.%s" %
                                (image.name,))

        self.log.info('Running %s' % cmd)

        try:
            p = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env)
        except OSError as e:
            raise exceptions.BuilderError(
                "Failed to exec '%s'. Error: '%s'" % (cmd, e.strerror)
            )

        while True:
            ln = p.stdout.readline()
            log.info(ln.strip())
            if not ln:
                break

        p.wait()
        ret = p.returncode
        if ret:
            raise exceptions.DibFailedError(
                "DIB failed creating %s" % filename
            )

        if self.statsd:
            # record stats on the size of each image we create
            for ext in img_types.split(','):
                key = 'nodepool.dib_image_build.%s.%s.size' % (image.name, ext)
                # A bit tricky because these image files may be sparse
                # files; we only want the true size of the file for
                # purposes of watching if we've added too much stuff
                # into the image.  Note that st_blocks is defined as
                # 512-byte blocks by stat(2)
                size = os.stat("%s.%s" % (filename, ext)).st_blocks * 512
                self.log.debug("%s created image %s.%s (size: %d) " %
                               (image.name, filename, ext, size))
                self.statsd.gauge(key, size)

    def _getDiskimageByName(self, name):
        for image in self._config.diskimages.values():
            if image.name == name:
                return image
        return None

    def buildImage(self, image_name, image_id):
        diskimage = self._getDiskimageByName(image_name)
        if diskimage is None:
            raise exceptions.BuilderInvalidCommandError(
                'Could not find matching image in config for %s', image_name
            )

        start_time = time.time()
        image_file = DibImageFile(image_id)
        filename = image_file.to_path(self._config.imagesdir, False)

        self.log.info("Creating image %s with filename %s" %
                      (diskimage.name, filename))
        self._runDibForImage(diskimage, filename)

        self.log.info("DIB image %s with filename %s is built" % (
            image_name, filename))

        if self.statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.dib_image_build.%s' % diskimage.name
            self.statsd.timing(key, dt)
            self.statsd.incr(key)
