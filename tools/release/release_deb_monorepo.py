#!/usr/bin/env python3
# This file is part of Checkbox.
#
# Copyright 2022-2023 Canonical Ltd.
# Written by:
#   Sylvain Pineau <sylvain.pineau@canonical.com>
#
# Checkbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# Checkbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Checkbox.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import json
import logging
import os
import shutil
import subprocess

from pathlib import Path


class ConsoleFormatter(logging.Formatter):

    """Custom Logging Formatter to ease copy paste of commands."""

    def format(self, record):
        fmt = '%(message)s'
        if record.levelno == logging.ERROR:
            fmt = "%(levelname)-8s %(message)s"
        result = logging.Formatter(fmt).format(record)
        return result


# create logger
logger = logging.getLogger('release')
logger.setLevel(logging.DEBUG)
# create file handler which logs even debug messages
fh = logging.FileHandler('release.log')
fh.setLevel(logging.DEBUG)
# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
# create formatter and add it to the handlers
fh_formatter = logging.Formatter('%(asctime)-15s %(levelname)-8s %(message)s')
fh.setFormatter(fh_formatter)
ch.setFormatter(ConsoleFormatter())
# add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)


def run(*args, **kwargs):
    """wrapper for subprocess.run."""
    try:
        return subprocess.run(
            *args, **kwargs,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        logger.error('{}\n{}'.format(e, e.output.decode()))
        raise SystemExit(1)


class Release:

    def __init__(self, args) -> None:
        self.args = args
        self.settings = {
            "dry_run": False,
            "mode": "testing",
            "root": None,
            "projects": {}
        }
        self._checkpoint = Path('.checkpoint.json')

    def _save_checkpoint(self):
        with open(self._checkpoint, "w") as f:
            json.dump(self.settings, f, indent=4, sort_keys=True)

    def _load_checkpoint(self):
        self.settings = json.load(open(self._checkpoint))

    def clone(self):
        config = json.load(open(self.args.config))
        mode = config["mode"]
        logger.info("".center(80, '#'))
        logger.info("# Parsing local repo...")
        logger.info("".center(80, '#'))
        for path, dirs, files in os.walk('.'):
            if "debian" in dirs:
                project_path = str(Path(*Path(path).parts))
                project_root = '.'
                project_name = str(project_path).replace('s/', '-')
                if not config[project_name]:
                    self.settings["projects"][project_name] = {
                        "path": path,
                        "release_required": False,
                        "last_tag": None,
                    }
                    logger.info(
                        "- No release requested: {}".format(project_name))
                    continue
                # Find the last stable tag
                cmd = run([
                    'git', 'for-each-ref', '--sort=-taggerdate', '--count=1',
                    'refs/tags/{}-v*[^c][0-9]'.format(project_name),
                    '--format=%(refname:short)'],
                    cwd=project_root)
                if cmd.returncode:
                    logger.warning("No tag found for {}".format(project_name))
                    continue
                last_stable_tag = cmd.stdout.decode().rstrip()
                # Find the last tag
                cmd = run([
                    'git', 'for-each-ref', '--sort=-taggerdate', '--count=1',
                    'refs/tags/{}-v*'.format(project_name),
                    '--format=%(refname:short)'],
                    cwd=project_root)
                if cmd.returncode:
                    logger.warning("No tag found for {}".format(project_name))
                    continue
                last_tag = cmd.stdout.decode().rstrip()
                # Ignore the release bump commit when looking for code changes
                bump_commit = 1
                if 'rc' in last_tag:
                    bump_commit = 0
                cmd = ['git', 'rev-list', '--count', '--no-merges',
                       '{}..'.format(last_stable_tag), '--',
                       "':{}'".format(project_path)]
                # TODO remove the exclude pathspec on debian after
                # the next release
                cmd += ["':(exclude){}/debian'".format(project_path)]
                count = int(run(
                    ' '.join(cmd), cwd=project_root, shell=True
                    ).stdout.decode().rstrip())
                is_release_required = False
                if mode == 'stable' and (count > bump_commit):
                    is_release_required = True
                if mode == 'testing' and (count > bump_commit):
                    is_release_required = True
                if is_release_required:
                    logger.info(
                        "+ Release required: {} (since {})".format(
                            project_name, last_stable_tag))
                else:
                    logger.info(
                        "- No release required: {}".format(project_name))
                self.settings["projects"][project_name] = {
                    "path": path,
                    "release_required": is_release_required,
                    "last_tag": last_tag,
                    "last_stable_tag": last_stable_tag,
                }
                self.settings['root'] = project_root
        self.settings['mode'] = config['mode']
        self.settings['dry_run'] = config['dry_run']
        self._save_checkpoint()

    def bump(self):
        """Bump project version and tag according to release mode."""
        self._load_checkpoint()
        mode = self.settings["mode"]
        root_dir = self.settings["root"]
        logger.info("".center(80, '#'))
        if mode == 'stable':
            logger.info("# Bump versions and add stable tags...")
        else:
            logger.info("# Bump versions and add release candidate tags...")
        logger.info("".center(80, '#'))
        for project_name, project_info in self.settings["projects"].items():
            if not project_info["release_required"]:
                continue
            last_tag = project_info["last_tag"].split('-v')[1]
            project_path = project_info["path"]
            if mode == 'stable':
                if "rc" in last_tag:
                    # Dry run to record the next stable version
                    bumpversion_output = run(
                        ['bumpversion', '--list', '--dry-run',
                         '--current-version', last_tag, '--no-commit',
                         'release'],
                        cwd=project_path, check=True).stdout.decode()
                    stable_version = \
                        bumpversion_output.splitlines()[-1].replace(
                            'new_version=', '')
                    message = "Bump {} version: {} → {}".format(
                        project_name, last_tag, stable_version)
                    stable_sha1 = run(
                        'git rev-list -n 1 '+project_info["last_tag"],
                        cwd=root_dir,
                        shell=True, check=True).stdout.decode().rstrip()
                    run(['git', 'tag', project_name+'-v'+stable_version,
                         stable_sha1, '-m', message],
                        cwd=root_dir, check=True)
                else:
                    continue
                    # FIXME Releasing to stable from an old stable tag w/o any
                    # RC in between must not be allowed, as it means silently
                    # relasing unverified commits.
                    # TODO log a warning
                    # run(['bumpversion', '--current-version', last_tag,
                    #         '--tag', '--no-commit', self.args.bump,
                    #         '--serialize',
                    #         '{major}.{minor}.{patch}'],
                    #     cwd=project_path, check=True)
            elif mode == 'testing':
                if "rc" in last_tag:
                    # bump to jump to rc+1
                    run(['bumpversion', '--current-version', last_tag,
                         '--tag', '--no-commit', 'N'],
                        cwd=project_path, check=True)
                else:
                    # bump to jump to rc1
                    run(['bumpversion', '--current-version',
                         '1.99.0',  # FIXME last_tag,
                         '--tag', '--no-commit', self.args.part],
                        cwd=project_path, check=True)
            # Only keep the tags, discard .bumpversion file updates
            run('git checkout .',
                cwd=project_path, check=True, shell=True)
            cmd = run([
                'git', 'for-each-ref', '--sort=-taggerdate', '--count=1',
                'refs/tags/{}-v*'.format(project_name),
                '--format=%(refname:short)'],
                cwd=root_dir)
            last_tag = cmd.stdout.decode().rstrip()
            project_info["last_tag"] = last_tag
            logger.info("Bump {} to {}".format(project_name, last_tag))
        self._save_checkpoint()

    def push(self):
        """Push release tags to origin."""
        self._load_checkpoint()
        logger.info("".center(80, '#'))
        logger.info("# Push release tags to origin...")
        logger.info("".center(80, '#'))
        root_dir = self.settings["root"]
        if self.settings["dry_run"]:
            run(
                ['git', 'push', '--dry-run', 'origin', '--tags'],
                cwd=root_dir, check=True)
        else:
            run(['git', 'push', 'origin', '--tags'], cwd=root_dir, check=True)

    def build(self):
        """Update the PPA recipe and kick-off the builds."""
        self._load_checkpoint()
        logger.info("".center(80, '#'))
        logger.info("# Update PPA recipes and kick-off the builds")
        logger.info("".center(80, '#'))
        if self.settings["dry_run"]:
            logger.info('# Dry run: Skipping the build step')
            return
        # Request code import
        staging = ""
        if self.args.staging:
            staging = "-staging"
        logger.info("Requesting code import...")
        output = run(
            "./tools/release/lp-request-import.py {}".format(
                "~checkbox-dev/checkbox/+git/checkbox"+staging),
            shell=True, check=True).stdout.decode().rstrip()
        logger.info(output)
        mode = self.settings["mode"]
        for project_name, project_info in self.settings["projects"].items():
            package_name = project_name
            if project_name.startswith('provider'):
                package_name = "checkbox-"+package_name
            if self.args.staging:
                package_name = "staging-"+package_name
            if not project_info["release_required"]:
                continue
            logger.info("Request {} build ({})".format(
                package_name, project_info["last_tag"]))
            new_version = project_info["last_tag"].split('-v')[1]
            if mode == 'testing':
                output = run(
                    "./tools/release/lp-recipe-update-build.py checkbox "
                    "--recipe {} -n {}".format(
                        package_name+'-testing', new_version),
                    shell=True, check=True).stdout.decode().rstrip()
            else:
                output = run(
                    "./tools/release/lp-recipe-update-build.py checkbox "
                    "--recipe {} -n {}".format(
                        package_name+'-stable', new_version),
                    shell=True, check=True).stdout.decode().rstrip()
            logger.info(output)

    def changelog(self):
        """Create the changelog for released projects."""
        self._load_checkpoint()
        logger.info("".center(80, '#'))
        logger.info("# Create the changelog...")
        logger.info("".center(80, '#'))
        root_dir = self.settings["root"]
        for project_name, project_info in self.settings["projects"].items():
            if not project_info["release_required"]:
                continue
            project_path = str(Path(*Path(project_info["path"]).parts[2:]))
            cmd = ['git', 'log', '--no-merges', "--pretty='format:+ %s'",
                   '{}...{}'.format(
                       project_info["last_stable_tag"],
                       project_info["last_tag"]),
                   '--', "':{}'".format(project_path)]
            # TODO remove the exclude pathspec on debian after
            # the next release
            cmd += ["':(exclude){}/debian'".format(project_path)]
            log = run(
                ' '.join(cmd), cwd=root_dir,
                check=True, shell=True).stdout.decode()
            if log:
                logger.debug("# {}: {}".format(project_name, cmd))
                with open('changelog', 'a') as f:
                    f.write('\n{} ({}):\n'.format(
                        project_name, project_info["last_tag"]))
                    f.write(log)
                    f.write("\n")

    def open(self):
        """Bump the project version to open a new release for development."""
        self._load_checkpoint()
        logger.info("".center(80, '#'))
        logger.info("# Open next versions for development...")
        logger.info("".center(80, '#'))
        self._checkpoint.unlink()
        if self.settings["mode"] == 'testing':
            logger.info(
                '# Impossible to open a new version for development'
                ', skipping...')
            return
        root_dir = self.settings["root"]
        run(['git', 'checkout', '-b', 'release'], cwd=root_dir, check=True)
        for project_name, project_info in self.settings["projects"].items():
            if not project_info["release_required"]:
                continue
            project_path = project_info["path"]
            # Dry run to record the next version number
            bumpversion_output = run(
                ['bumpversion', '--list', '--dry-run',
                 self.args.part, '--serialize',
                 '{major}.{minor}.{patch}'],
                cwd=project_path, check=True).stdout.decode()
            new_dev_version = bumpversion_output.splitlines()[-1].replace(
                'new_version=', '')
            logger.info("Bump {} to version {}".format(
                project_name, new_dev_version))
            run(['dch', '-v', new_dev_version, '-D', 'unstable',
                 '"new upstream version"'],
                cwd=project_path, check=True)
            run('git add --update',
                cwd=project_path, check=True, shell=True)
            files = [
                'setup.py', 'plainbox/__init__.py', 'checkbox_ng/__init__.py']
            if project_name == 'checkbox-support':
                files = ['setup.py']
            elif 'provider' in project_name:
                files = ['manage.py']
            message = "Bump {} version: ".format(
                project_name) + "{current_version} → {new_version}"
            run(['bumpversion', '--serialize', '{major}.{minor}.{patch}',
                 '--message', message,
                 self.args.part, '--allow-dirty', '--commit'] + files,
                cwd=project_path, check=True)
        if self.settings["dry_run"]:
            run(
                ['git', 'push', '--dry-run', 'origin', 'release'],
                cwd=root_dir, check=True)
        else:
            run(
                ['git', 'push', '-f', 'origin', 'release'],
                cwd=root_dir, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Release Debian packages from checkbox monorepo")
    subparsers = parser.add_subparsers(
        dest='step', help='release step to run')
    parser_clone = subparsers.add_parser(
        'clone', help='Clone and check last tags')
    parser_clone.add_argument(
        "--config", required=True,
        help="Release settings (json file)", metavar="CONFIG")
    parser_bump = subparsers.add_parser(
        'bump', help='Bump packages version')
    parser_bump.add_argument("--part", default='minor', help='bump part name')
    subparsers.add_parser(
        'push', help='Push release tags to origin')
    parser_build = subparsers.add_parser(
        'build', help='Start Launchpad build recipes')
    parser_build.add_argument(
        "--staging", action='store_true', help='Use the staging ppa')
    subparsers.add_parser(
        'changelog', help='Generate release changelog')
    parser_open = subparsers.add_parser(
        'open', help='Open next version for development')
    parser_open.add_argument("--part", default='minor', help='bump part name')
    # Main options
    parser.add_argument("-u", "--user",
                        help="Specify launchpad user id", metavar="USER")
    parser.add_argument(
        '--target-user', default='checkbox-dev',
        help=("target repositories owner (default: %(default)s)"))
    parser.add_argument("--credentials",
                        help="Specify launchpad credentials", metavar="CRED")
    args = parser.parse_args()

    # Run the release step command
    getattr(Release(args), args.step)()


if __name__ == "__main__":
    main()
