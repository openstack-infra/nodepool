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

import nodedb

MINS = 60
HOURS = 60 * MINS
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save

# HP Cloud requires qemu compat with 0.10. That version works elsewhere,
# so just hardcode it for all qcow2 building
DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS = "--qemu-img-options 'compat=0.10'"


class NodePoolBuilder(object):
    log = logging.getLogger("nodepool.builder")

    def __init__(self, nodepool):
        self.nodepool = nodepool
        self._running = False
        self._built_image_ids = set()
        self._start_lock = threading.Lock()
        self._gearman_worker = None

    @property
    def running(self):
        return self._running

    def start(self):
        with self._start_lock:
            if self._running:
                raise RuntimeError('Cannot start, already running.')
            self._running = True

            self._gearman_worker = self._initializeGearmanWorker(
                self.nodepool.config.gearman_servers.values())
            images = self.nodepool.config.diskimages.keys()
            self._registerGearmanFunctions(images)
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
            self._gearman_worker.registerFunction('image-build:%s' % image)

    def _registerExistingImageUploads(self):
        with self.nodepool.getDB().getSession() as session:
            for image in session.getDibImages():
                self.registerImageId(image.id)

    def _registerImageId(self, image_id):
        self.log.debug('registering image %d', image_id)
        self._gearman_worker.registerFunction('image-upload:%s' % image_id)
        self._gearman_worker.registerFunction('image-delete:%s' % image_id)
        self._built_image_ids.add(image_id)

    def _unregisterImageId(self, worker, image_id):
        if image_id in self._built_image_ids:
            self.log.debug('unregistering image %d', image_id)
            worker.unRegisterFunction('image-upload:%d' % image_id)
            self._built_image_ids.remove(image_id)
        else:
            self.log.warning('Attempting to remove image %d but image not '
                             'found', image_id)

    def _canHandleImageidJob(self, job, image_op):
        return (job.name.startswith(image_op + ':') and
                int(job.name.split(':')[1]) in self._built_image_ids)

    def _handleJob(self, job):
        try:
            self.log.debug('got job %s with data %s',
                           job.name, job.arguments)
            if job.name.startswith('image-build:'):
                args = json.loads(job.arguments)
                self.buildImage(args['image_id'])

                # We can now upload this image
                self._registerImageId(args['image_id'])
                job.sendWorkComplete()
            elif self._canHandleImageidJob(job, 'image-upload'):
                args = json.loads(job.arguments)
                image_id = job.name.split(':')[1]
                external_id = self._uploadImage(int(image_id),
                                                 args['provider'])
                job.sendWorkComplete(json.dumps({'external-id': external_id}))
            elif self._canHandleImageidJob(job, 'image-delete'):
                image_id = job.name.split(':')[1]
                self._deleteImage(int(image_id))
                self._unregisterImageId(self._gearman_worker, int(image_id))
                job.sendWorkComplete()
            else:
                self.log.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.log.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())

    def _deleteImage(self, image_id):
        with self.nodepool.getDB().getSession() as session:
            dib_image = session.getDibImage(image_id)

            # Remove image from the nodedb
            dib_image.state = nodedb.DELETE
            dib_image.delete()

            config = self.nodepool.config
            image_config = config.diskimages.get(dib_image.image_name)
            if not image_config:
                self.log.error("Deleting image %d but configuration not found."
                               "Cannot delete image from disk without a "
                               "configuration.", image_id)
                return
            # Delete a dib image and its associated file
            for image_type in image_config.image_types:
                if os.path.exists(dib_image.filename + '.' + image_type):
                    os.remove(dib_image.filename + '.' + image_type)

            self.log.info("Deleted dib image id: %s" % dib_image.id)

    def _uploadImage(self, image_id, provider_name):
        with self.nodepool.getDB().getSession() as session:
            start_time = time.time()
            timestamp = int(start_time)

            dib_image = session.getDibImage(image_id)
            provider = self.nodepool.config.providers[provider_name]

            filename = dib_image.filename

            dummy_image = type('obj', (object,),
                               {'name': dib_image.image_name})
            image_name = provider.template_hostname.format(
                provider=provider, image=dummy_image, timestamp=str(timestamp))
            self.log.info("Uploading dib image id: %s from %s for %s in %s" %
                          (dib_image.id, filename, image_name, provider.name))

            manager = self.nodepool.getProviderManager(provider)
            provider_image = filter(
                lambda x: x.diskimage == dib_image.image_name,
                provider.images.values())
            if len(provider_image) != 1:
                self.log.error("Could not find a matching provider image for "
                               "%s", dib_image.image_name)
                return
            provider_image = provider_image[0]

            image_meta = provider_image.meta
            provider_image_id = manager.uploadImage(image_name, filename,
                                                    provider.image_type,
                                                    'bare', image_meta)
            self.log.debug("Image id: %s saving image %s" %
                           (dib_image.id, provider_image_id))
            # It can take a _very_ long time for Rackspace 1.0 to save an image
            manager.waitForImage(provider_image_id, IMAGE_TIMEOUT)

            if statsd:
                dt = int((time.time() - start_time) * 1000)
                key = 'nodepool.image_update.%s.%s' % (image_name,
                                                       provider.name)
                statsd.timing(key, dt)
                statsd.incr(key)

            self.log.info("Image %s in %s is ready" % (dib_image.image_name,
                                                       provider.name))
            return provider_image_id

    def _buildImage(self, image, image_name, filename):
        env = os.environ.copy()

        env['DIB_RELEASE'] = image.release
        env['DIB_IMAGE_NAME'] = image_name
        env['DIB_IMAGE_FILENAME'] = filename
        # Note we use a reference to the nodepool config here so
        # that whenever the config is updated we get up to date
        # values in this thread.
        if self.nodepool.config.elementsdir:
            env['ELEMENTS_PATH'] = self.nodepool.config.elementsdir
        if self.nodepool.config.scriptdir:
            env['NODEPOOL_SCRIPTDIR'] = self.nodepool.config.scriptdir

        # send additional env vars if needed
        for k, v in image.env_vars.items():
            env[k] = v

        img_elements = image.elements
        img_types = ",".join(image.image_types)

        qemu_img_options = ''
        if 'qcow2' in img_types:
            qemu_img_options = DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS

        if 'fake-' in filename:
            dib_cmd = 'nodepool/tests/fake-image-create'
        else:
            dib_cmd = 'disk-image-create'

        cmd = ('%s -x -t %s --no-tmpfs %s -o %s %s' %
               (dib_cmd, img_types, qemu_img_options, filename, img_elements))

        log = logging.getLogger("nodepool.image.build.%s" %
                                (image_name,))

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

    def buildImage(self, image_id):
        with self.nodepool.getDB().getSession() as session:
            image = session.getDibImage(image_id)
            self.log.info("Creating image: %s with filename %s" %
                          (image.image_name, image.filename))

            start_time = time.time()
            timestamp = int(start_time)
            image.version = timestamp
            session.commit()

            # retrieve image details
            image_details = \
                self.nodepool.config.diskimages[image.image_name]
            try:
                self._buildImage(
                    image_details,
                    image.image_name,
                    image.filename)
            except Exception:
                self.log.exception("Exception building DIB image %s:" %
                                   (image_id,))
                # DIB should've cleaned up after itself, just remove this
                # image from the DB.
                image.delete()
                return

            image.state = nodedb.READY
            session.commit()
            self.log.info("DIB image %s with file %s is built" % (
                image_id, image.filename))

            if statsd:
                dt = int((time.time() - start_time) * 1000)
                key = 'nodepool.dib_image_build.%s' % image.image_name
                statsd.timing(key, dt)
                statsd.incr(key)
