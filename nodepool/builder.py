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

import errno
import json
import logging
import os
import subprocess
import threading
import time
import traceback

import gear
import shlex

import config as nodepool_config
import exceptions
import provider_manager
import stats

MINS = 60
HOURS = 60 * MINS
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save

# HP Cloud requires qemu compat with 0.10. That version works elsewhere,
# so just hardcode it for all qcow2 building
DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS = "--qemu-img-options 'compat=0.10'"


class DibImageFile(object):
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


class BaseWorker(gear.Worker):
    nplog = logging.getLogger("nodepool.builder.BaseWorker")

    def __init__(self, *args, **kw):
        self.builder = kw.pop('builder')
        super(BaseWorker, self).__init__(*args, **kw)

    def run(self):
        while self.running:
            try:
                job = self.getJob()
                self._handleJob(job)
            except gear.InterruptedError:
                pass
            except Exception:
                self.nplog.exception('Exception while getting job')


class BuildWorker(BaseWorker):
    nplog = logging.getLogger("nodepool.builder.BuildWorker")

    def _handleJob(self, job):
        try:
            self.nplog.debug('Got job %s with data %s',
                             job.name, job.arguments)
            if job.name.startswith('image-build:'):
                args = json.loads(job.arguments)
                image_id = args['image-id']
                if '/' in image_id:
                    raise exceptions.BuilderInvalidCommandError(
                        'Invalid image-id specified'
                    )

                image_name = job.name.split(':', 1)[1]
                try:
                    self.builder.buildImage(image_name, image_id)
                except exceptions.BuilderError:
                    self.nplog.exception('Exception while building image')
                    job.sendWorkFail()
                else:
                    # We can now upload this image
                    self.builder.registerImageId(image_id)
                    job.sendWorkComplete(json.dumps({'image-id': image_id}))
            else:
                self.nplog.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.nplog.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())


class UploadWorker(BaseWorker):
    nplog = logging.getLogger("nodepool.builder.UploadWorker")

    def _handleJob(self, job):
        try:
            self.nplog.debug('Got job %s with data %s',
                             job.name, job.arguments)
            if self.builder.canHandleImageIdJob(job, 'image-upload'):
                args = json.loads(job.arguments)
                image_name = args['image-name']
                image_id = job.name.split(':')[1]
                try:
                    external_id = self.builder.uploadImage(image_id,
                                                           args['provider'],
                                                           image_name)
                except exceptions.BuilderError:
                    self.nplog.exception('Exception while uploading image')
                    job.sendWorkFail()
                else:
                    job.sendWorkComplete(
                        json.dumps({'external-id': external_id})
                    )
            elif self.builder.canHandleImageIdJob(job, 'image-delete'):
                image_id = job.name.split(':')[1]
                self.builder.deleteImage(image_id)
                self.builder.unregisterImageId(image_id)
                job.sendWorkComplete()
            else:
                self.nplog.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.nplog.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())


class NodePoolBuilder(object):
    log = logging.getLogger("nodepool.builder")

    def __init__(self, config_path, build_workers=1, upload_workers=4):
        self._config_path = config_path
        self._running = False
        self._built_image_ids = set()
        self._start_lock = threading.Lock()
        self._config = None
        self._build_workers = build_workers
        self._upload_workers = upload_workers
        self.statsd = stats.get_client()

    @property
    def running(self):
        return self._running

    def runForever(self):
        with self._start_lock:
            if self._running:
                raise exceptions.BuilderError('Cannot start, already running.')

            self.load_config(self._config_path)
            self._validate_config()

            self.build_workers = []
            self.upload_workers = []
            for i in range(self._build_workers):
                w = BuildWorker('Nodepool Builder Build Worker %s' % (i+1,),
                                builder=self)
                self._initializeGearmanWorker(w,
                    self._config.gearman_servers.values())
                self.build_workers.append(w)
            for i in range(self._upload_workers):
                w = UploadWorker('Nodepool Builder Upload Worker %s' % (i+1,),
                                 builder=self)
                self._initializeGearmanWorker(w,
                    self._config.gearman_servers.values())
                self.upload_workers.append(w)

            self._registerGearmanFunctions(self._config.diskimages.values())
            self._registerExistingImageUploads()

            self.log.debug('Starting listener for build jobs')

            self.threads = []
            for worker in self.build_workers + self.upload_workers:
                t = threading.Thread(target=worker.run)
                t.daemon = True
                t.start()
                self.threads.append(t)

            self._running = True

        for t in self.threads:
            t.join()

        self._running = False

    def stop(self):
        with self._start_lock:
            self.log.debug('Stopping.')
            if not self._running:
                self.log.warning("Stop called when already stopped")
                return

            for worker in self.build_workers + self.upload_workers:
                try:
                    worker.shutdown()
                except OSError as e:
                    if e.errno == errno.EBADF:
                        # The connection has been lost already
                        self.log.debug("Gearman connection lost when "
                                       "attempting to shutdown; ignoring")
                    else:
                        raise

            self.log.debug('Waiting for jobs to complete')
            # Wait for the builder to complete any currently running jobs
            while self._running:
                time.sleep(1)

            self.log.debug('Stopping providers')
            provider_manager.ProviderManager.stopProviders(self._config)
            self.log.debug('Finished stopping')

    def load_config(self, config_path):
        config = nodepool_config.loadConfig(config_path)
        provider_manager.ProviderManager.reconfigure(
            self._config, config)
        self._config = config

    def _validate_config(self):
        if not self._config.gearman_servers.values():
            raise RuntimeError('No gearman servers specified in config.')

        if not self._config.imagesdir:
            raise RuntimeError('No images-dir specified in config.')

    def _initializeGearmanWorker(self, worker, servers):
        for server in servers:
            worker.addServer(server.host, server.port)

        self.log.debug('Waiting for gearman server')
        worker.waitForServer()

    def _registerGearmanFunctions(self, images):
        self.log.debug('Registering gearman functions')
        for worker in self.build_workers:
            for image in images:
                worker.registerFunction(
                    'image-build:%s' % image.name)

    def _registerExistingImageUploads(self):
        images = DibImageFile.from_images_dir(self._config.imagesdir)
        for image in images:
            self.registerImageId(image.image_id)

    def registerImageId(self, image_id):
        self.log.info('Registering image id: %s', image_id)
        for worker in self.upload_workers:
            worker.registerFunction('image-upload:%s' % image_id)
            worker.registerFunction('image-delete:%s' % image_id)
        self._built_image_ids.add(image_id)

    def unregisterImageId(self, image_id):
        if image_id in self._built_image_ids:
            self.log.info('Unregistering image id: %s', image_id)
            for worker in self.upload_workers:
                worker.unRegisterFunction('image-upload:%s' % image_id)
                worker.unRegisterFunction('image-delete:%s' % image_id)
            self._built_image_ids.remove(image_id)
        else:
            self.log.warning('Attempting to remove image %d but image not '
                             'found', image_id)

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
