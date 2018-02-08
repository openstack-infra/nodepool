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
import logging
import logging.config
import os
import signal
import sys
import threading
import traceback

from nodepool.version import version_info as npd_version_info
from nodepool import logconfig

# as of python-daemon 1.6 it doesn't bundle pidlockfile anymore
# instead it depends on lockfile-0.9.1 which uses pidfile.
pid_file_module = extras.try_imports(['daemon.pidlockfile', 'daemon.pidfile'])


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


class NodepoolApp(object):

    app_name = None
    app_description = 'Node pool.'

    def __init__(self):
        self.parser = None
        self.args = None

    def get_path(self, path):
        if path is None:
            return None
        return os.path.abspath(os.path.expanduser(path))

    def create_parser(self):
        parser = argparse.ArgumentParser(description=self.app_description)

        parser.add_argument('-l',
                            dest='logconfig',
                            help='path to log config file')

        parser.add_argument('--version',
                            action='version',
                            version=npd_version_info.version_string())

        return parser

    def parse_args(self):
        args = self.parser.parse_args()
        self.logconfig = self.get_path(args.logconfig)
        return args

    def setup_logging(self):
        if self.logconfig:
            logging_config = logconfig.load_config(self.logconfig)
        else:
            # If someone runs in the foreground and doesn't give a logging
            # config, leave the config set to emit to stdout.
            if hasattr(self.args, 'nodaemon') and self.args.nodaemon:
                logging_config = logconfig.ServerLoggingConfig()
                logging_config.setDebug()
            else:
                # Setting a server value updates the defaults to use
                # WatchedFileHandler on /var/log/nodepool/{server}-debug.log
                # and /var/log/nodepool/{server}.log
                logging_config = logconfig.ServerLoggingConfig(
                    server=self.app_name)
        logging_config.apply()

    def _main(self, argv=None):
        if argv is None:
            argv = sys.argv[1:]

        self.parser = self.create_parser()
        self.args = self.parse_args()
        return self._do_run()

    def _do_run(self):
        # NOTE(jamielennox): setup logging a bit late so it's not done until
        # after a DaemonContext is created.
        self.setup_logging()
        return self.run()

    @classmethod
    def main(cls, argv=None):
        return cls()._main(argv=argv)

    def run(self):
        """The app's primary function, override it with your logic."""
        raise NotImplementedError()


class NodepoolDaemonApp(NodepoolApp):

    def create_parser(self):
        parser = super(NodepoolDaemonApp, self).create_parser()

        parser.add_argument('-p',
                            dest='pidfile',
                            help='path to pid file',
                            default='/var/run/nodepool/%s.pid' % self.app_name)

        parser.add_argument('-d',
                            dest='nodaemon',
                            action='store_true',
                            help='do not run as a daemon')

        return parser

    def parse_args(self):
        args = super(NodepoolDaemonApp, self).parse_args()
        self.pidfile = self.get_path(args.pidfile)
        return args

    def _do_run(self):
        if self.args.nodaemon:
            return super(NodepoolDaemonApp, self)._do_run()

        else:
            pid = pid_file_module.TimeoutPIDLockFile(self.pidfile, 10)

            if is_pidfile_stale(pid):
                pid.break_lock()

            # Exercise the pidfile before we do anything else (including
            # logging or daemonizing)
            with pid:
                pass

            with daemon.DaemonContext(pidfile=pid):
                return super(NodepoolDaemonApp, self)._do_run()

    @classmethod
    def main(cls, argv=None):
        signal.signal(signal.SIGUSR2, stack_dump_handler)
        return super(NodepoolDaemonApp, cls).main(argv)
