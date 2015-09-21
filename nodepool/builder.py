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
from stats import statsd

from config import loadConfig

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
                raise ValueError('Cannot specify image extension of None')
            my_path += '.' + self.extension
        return my_path


class NodePoolBuilder(object):
    log = logging.getLogger("nodepool.builder")

    def __init__(self, config_path, nodepool):
        self._config_path = config_path
        self.nodepool = nodepool
        self._running = False
        self._built_image_ids = set()
        self._start_lock = threading.Lock()
        self._gearman_worker = None
        self._config = None

    @property
    def running(self):
        return self._running

    def start(self):
        self.load_config(self._config_path)

        with self._start_lock:
            if self._running:
                raise RuntimeError('Cannot start, already running.')
            self._running = True

            self._gearman_worker = self._initializeGearmanWorker(
                self._config.gearman_servers.values())
            self._registerGearmanFunctions(self._config.diskimages.values())
            self._registerExistingImageUploads()
            self.thread = threading.Thread(target=self._run,
                                           name='NodePool Builder')
            self.thread.start()

    def stop(self):
        with self._start_lock:
            self.log.debug('Stopping')
            if not self._running:
                self.log.warning("Stop called when we are already stopped.")
                return

            self._running = False

            self._gearman_worker.stopWaitingForJobs()

            self.log.debug('Waiting for builder thread to complete.')
            self.thread.join()
            self.log.debug('Builder thread completed.')

            try:
                self._gearman_worker.shutdown()
            except OSError as e:
                if e.errno == errno.EBADF:
                    # The connection has been lost already
                    self.log.debug("Gearman connection lost when "
                                   "attempting to shutdown. Ignoring.")
                else:
                    raise

    def load_config(self, config_path):
        self._config = loadConfig(config_path)

    def _run(self):
        self.log.debug('Starting listener for build jobs')
        while self._running:
            try:
                job = self._gearman_worker.getJob()
                self._handleJob(job)
            except gear.InterruptedError:
                pass
            except Exception:
                self.log.exception('Exception while getting job')

    def _initializeGearmanWorker(self, servers):
        worker = gear.Worker('Nodepool Builder')
        for server in servers:
            worker.addServer(server.host, server.port)

        self.log.debug('Waiting for gearman server')
        worker.waitForServer()
        return worker

    def _registerGearmanFunctions(self, images):
        self.log.debug('Registering gearman functions')
        for image in images:
            self._gearman_worker.registerFunction(
                'image-build:%s' % image.name)

    def _registerExistingImageUploads(self):
        images = DibImageFile.from_images_dir(self._config.imagesdir)
        for image in images:
            self._registerImageId(image.image_id)

    def _registerImageId(self, image_id):
        self.log.debug('registering image %s', image_id)
        self._gearman_worker.registerFunction('image-upload:%s' % image_id)
        self._gearman_worker.registerFunction('image-delete:%s' % image_id)
        self._built_image_ids.add(image_id)

    def _unregisterImageId(self, worker, image_id):
        if image_id in self._built_image_ids:
            self.log.debug('unregistering image %s', image_id)
            worker.unRegisterFunction('image-upload:%s' % image_id)
            self._built_image_ids.remove(image_id)
        else:
            self.log.warning('Attempting to remove image %d but image not '
                             'found', image_id)

    def _canHandleImageidJob(self, job, image_op):
        return (job.name.startswith(image_op + ':') and
                job.name.split(':')[1]) in self._built_image_ids

    def _handleJob(self, job):
        try:
            self.log.debug('got job %s with data %s',
                           job.name, job.arguments)
            if job.name.startswith('image-build:'):
                args = json.loads(job.arguments)
                image_id = args['image-id']
                if '/' in image_id:
                    raise RuntimeError('Invalid image-id specified')

                image_name = job.name.split(':', 1)[1]
                try:
                    self._buildImage(image_name, image_id)
                except RuntimeError:
                    self.log.exception('Error building image')
                    job.sendWorkFail()
                else:
                    # We can now upload this image
                    self._registerImageId(image_id)
                    job.sendWorkComplete(json.dumps({'image-id': image_id}))
            elif self._canHandleImageidJob(job, 'image-upload'):
                args = json.loads(job.arguments)
                image_name = args['image-name']
                image_id = job.name.split(':')[1]
                external_id = self._uploadImage(image_id,
                                                args['provider'],
                                                image_name)
                job.sendWorkComplete(json.dumps({'external-id': external_id}))
            elif self._canHandleImageidJob(job, 'image-delete'):
                image_id = job.name.split(':')[1]
                self._deleteImage(image_id)
                self._unregisterImageId(self._gearman_worker, image_id)
                job.sendWorkComplete()
            else:
                self.log.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.log.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())

    def _deleteImage(self, image_id):
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

    def _uploadImage(self, image_id, provider_name, image_name):
        start_time = time.time()
        timestamp = int(start_time)

        provider = self._config.providers[provider_name]
        image_type = provider.image_type

        image_files = DibImageFile.from_image_id(self._config.imagesdir,
                                                 image_id)
        image_files = filter(lambda x: x.extension == image_type, image_files)
        if len(image_files) == 0:
            self.log.error("Unable to find image file for id %s to upload",
                           image_id)
            return
        if len(image_files) > 1:
            self.log.error("Found more than one image for id %s. This should "
                           "never happen.", image_id)

        image_file = image_files[0]
        filename = image_file.to_path(self._config.imagesdir,
                                      with_extension=False)

        dummy_image = type('obj', (object,),
                           {'name': image_name})
        ext_image_name = provider.template_hostname.format(
            provider=provider, image=dummy_image, timestamp=str(timestamp))
        self.log.info("Uploading dib image id: %s from %s in %s" %
                      (image_id, filename, provider.name))

        manager = self.nodepool.getProviderManager(provider)
        try:
            provider_image = provider.images[image_name]
        except KeyError:
            self.log.error("Could not find matching provider image for %s",
                           image_name)
            return
        image_meta = provider_image.meta
        external_id = manager.uploadImage(ext_image_name, filename,
                                          image_file.extension, 'bare',
                                          image_meta)
        self.log.debug("Saving image id: %s", external_id)
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        manager.waitForImage(external_id, IMAGE_TIMEOUT)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (image_name,
                                                   provider.name)
            statsd.timing(key, dt)
            statsd.incr(key)

        self.log.info("Image %s in %s is ready" % (image_id,
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
            raise Exception("Failed to exec '%s'. Error: '%s'" %
                            (cmd, e.strerror))

        while True:
            ln = p.stdout.readline()
            log.info(ln.strip())
            if not ln:
                break

        p.wait()
        ret = p.returncode
        if ret:
            raise Exception("DIB failed creating %s" % (filename,))

    def _getDiskimageByName(self, name):
        for image in self._config.diskimages.values():
            if image.name == name:
                return image
        return None

    def _buildImage(self, image_name, image_id):
        diskimage = self._getDiskimageByName(image_name)
        if diskimage is None:
            raise RuntimeError('Could not find matching image in config for '
                               '%s', image_name)

        start_time = time.time()
        image_file = DibImageFile(image_id)
        filename = image_file.to_path(self._config.imagesdir, False)

        self.log.info("Creating image: %s with filename %s" %
                      (diskimage.name, filename))
        self._runDibForImage(diskimage, filename)

        self.log.info("DIB image %s with file %s is built" % (
            image_name, filename))

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.dib_image_build.%s' % diskimage.name
            statsd.timing(key, dt)
            statsd.incr(key)
