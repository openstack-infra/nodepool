#!/usr/bin/env python
# Copyright 2012 Hewlett-Packard Development Company, L.P.
# Copyright 2013 OpenStack Foundation
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

import argparse
import daemon
import errno
import extras

# as of python-daemon 1.6 it doesn't bundle pidlockfile anymore
# instead it depends on lockfile-0.9.1 which uses pidfile.
pid_file_module = extras.try_imports(['daemon.pidlockfile', 'daemon.pidfile'])

import logging.config
import os
import sys
import signal
import traceback
import threading

import nodepool.builder
import nodepool.cmd
import nodepool.nodepool


def stack_dump_handler(signum, frame):
    signal.signal(signal.SIGUSR2, signal.SIG_IGN)
    log_str = ""
    threads = {}
    for t in threading.enumerate():
        threads[t.ident] = t
    for thread_id, stack_frame in sys._current_frames().items():
        thread = threads.get(thread_id)
        if thread:
            thread_name = thread.name
        else:
            thread_name = 'Unknown'
        label = '%s (%s)' % (thread_name, thread_id)
        log_str += "Thread: %s\n" % label
        log_str += "".join(traceback.format_stack(stack_frame))
    log = logging.getLogger("nodepool.stack_dump")
    log.debug(log_str)
    signal.signal(signal.SIGUSR2, stack_dump_handler)


def is_pidfile_stale(pidfile):
    """ Determine whether a PID file is stale.

        Return 'True' ("stale") if the contents of the PID file are
        valid but do not match the PID of a currently-running process;
        otherwise return 'False'.

        """
    result = False

    pidfile_pid = pidfile.read_pid()
    if pidfile_pid is not None:
        try:
            os.kill(pidfile_pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                # The specified PID does not exist
                result = True

    return result


class NodePoolDaemon(nodepool.cmd.NodepoolApp):

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description='Node pool.')
        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            default='/etc/nodepool/secure.conf',
                            help='path to secure file')
        parser.add_argument('-d', dest='nodaemon', action='store_true',
                            help='do not run as a daemon')
        parser.add_argument('-l', dest='logconfig',
                            help='path to log config file')
        parser.add_argument('-p', dest='pidfile',
                            help='path to pid file',
                            default='/var/run/nodepool/nodepool.pid')
        parser.add_argument('--no-builder', dest='builder',
                            action='store_false')
        parser.add_argument('--build-workers', dest='build_workers',
                            default=1, help='number of build workers',
                            type=int)
        parser.add_argument('--upload-workers', dest='upload_workers',
                            default=4, help='number of upload workers',
                            type=int)
        parser.add_argument('--version', dest='version', action='store_true',
                            help='show version')
        self.args = parser.parse_args()

    def exit_handler(self, signum, frame):
        self.pool.stop()
        if self.args.builder:
            self.builder.stop()
        sys.exit(0)

    def term_handler(self, signum, frame):
        os._exit(0)

    def main(self):
        self.setup_logging()
        self.pool = nodepool.nodepool.NodePool(self.args.secure,
                                               self.args.config)
        if self.args.builder:
            self.builder = nodepool.builder.NodePoolBuilder(
                self.args.config, self.args.build_workers,
                self.args.upload_workers)

        signal.signal(signal.SIGINT, self.exit_handler)
        # For back compatibility:
        signal.signal(signal.SIGUSR1, self.exit_handler)

        signal.signal(signal.SIGUSR2, stack_dump_handler)
        signal.signal(signal.SIGTERM, self.term_handler)

        self.pool.start()
        if self.args.builder:
            nb_thread = threading.Thread(target=self.builder.runForever)
            nb_thread.start()

        while True:
            signal.pause()


def main():
    npd = NodePoolDaemon()
    npd.parse_arguments()

    if npd.args.version:
        from nodepool.version import version_info as npd_version_info
        print "Nodepool version: %s" % npd_version_info.version_string()
        return(0)

    pid = pid_file_module.TimeoutPIDLockFile(npd.args.pidfile, 10)
    if is_pidfile_stale(pid):
        pid.break_lock()

    if npd.args.nodaemon:
        npd.main()
    else:
        with daemon.DaemonContext(pidfile=pid):
            npd.main()


if __name__ == "__main__":
    sys.exit(main())
