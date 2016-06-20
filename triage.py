#!/usr/bin/python
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible. If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function

try:
    import argparse
except ImportError:
    print('argparse is required')

import logging
import os
# import shelve
import re
import sys
import time
from datetime import datetime
from collections import defaultdict

from github import Github
import find_traceback
import github_helpers


from jinja2 import Environment, FileSystemLoader

DEBUG_LOG_FORMAT = "%(asctime)s [%(name)s %(levelname)s] %(funcName)s:%(lineno)d - %(message)s"
logging.basicConfig(level=logging.DEBUG,
                    format=DEBUG_LOG_FORMAT)

# FIXME: move to __name__ once this isn't just __main__
log = logging.getLogger('ansibulbot')
loader = FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates'))
environment = Environment(loader=loader, trim_blocks=True)

TEAM = 'ansible'
# A dict of alias labels. It is used for coupling a template (comment) with a
# label.
PR_ALIAS_LABELS = {
    'core_review': [
        'core_review_existing'
    ],
    'community_review': [
        'community_review_existing',
        'community_review_new',
        'community_review_owner_pr',
    ],
    'shipit': [
        'shipit_owner_pr'
    ],
    'needs_revision': [
        'needs_revision_not_mergeable'
    ],
    'pending_action': [
        'pending_action_close_me',
        'pending_maintainer_unknown'
    ]
}

ISSUE_ALIAS_LABELS = {}


MAINTAINERS_FILES = {
    'ansible': "MAINTAINERS-ANSIBLE.txt",
    'core': "MAINTAINERS-CORE.txt",
    'extras': "MAINTAINERS-EXTRAS.txt",
}

REPO_NICKNAMES = {
    'core': 'ansible/ansible-modules-core',
    'extras': 'ansible/ansible-modules-extras',
    'ansible': 'ansible/ansible'
}

# modules having files starting like the key, will get the value label
MODULE_NAMESPACE_LABELS = {
    'cloud': "cloud",
    'windows': "windows",
    'network': "networking"
}

# We don't remove any of these labels unless forced
PR_MUTUALLY_EXCLUSIVE_LABELS = [
    "shipit",
    "needs_revision",
    "needs_info",
    "community_review",
    "core_review",
]

ISSUE_MUTUALLY_EXCLUSIVE_LABELS = []

# Static labels, manually added
IGNORE_LABELS = [
    "feature_pull_request",
    "bugfix_pull_request",
    "in progress",
    "docs_pull_request",
    "easyfix",
    "pending_action",
    "gce",
    "python3",
]

# We warn for human interaction
PR_MANUAL_INTERACTION_LABELS = [
    "needs_revision",
    "needs_info",
]

ISSUE_MANUAL_INTERACTION_LABELS = []

BOTLIST = [
    'gregdek',
    'robynbergeron',
    'alikins',
]

class TriageError(Exception):
    pass

# TODO: There should probably be a base class here that
#       Issue and PullRequest subclass

class BaseIssue(object):
    issue_type_name = 'BaseIssue'
    alias_labels = ISSUE_ALIAS_LABELS
    mutually_exclusive_labels = ISSUE_MUTUALLY_EXCLUSIVE_LABELS
    manual_interaction_labels = ISSUE_MANUAL_INTERACTION_LABELS

    def __init__(self, repo, number=None, issue=None):
        self.repo = repo

        # TODO, rename self.instance to self.gh_issue or gh_instance
        if not issue:
            self.instance = self._get_issue_type_instance(number)
        else:
            self.instance = issue

        log.debug("self.instance=%s", self.instance)
        self.number = self.instance.number

        # not true for prs
        self._issue = None

        self.current_labels = []
        self.desired_labels = []

        self.current_comments = []
        self.desired_comments = []

        self.filenames = []

    def _get_issue_type_instance(self, number):
        log.debug('Issue._get_i_t_i number=%s', number)
        return self.repo.get_issue(number)

    # shared
    def get_submitter(self):
        """Returns the PR submitter"""
        return self.instance.user.login

    # shared
    def is_labeled_for_interaction(self):
        """Returns True if PR is labeled for interaction"""
        for current_label in self.get_current_labels():
            if current_label in self.manual_interaction_labels:
                return True
        return False

    @property
    def issue(self):
        """Gets the issue from the GitHub API"""
        if not self._issue:
            self._issue = self.repo.get_issue(self.number)
        return self._issue

    # shared
    def get_current_labels(self):
        """Pull the list of labels on this PR and shove them into
        pr_labels.
        """
        if not self.current_labels:
            labels = self.issue.labels
            for label in labels:
                self.current_labels.append(label.name)
        return self.current_labels

    # shared
    def get_comments(self):
        """Returns all current comments of the PR"""
        if not self.current_comments:
            self.current_comments = self.instance.get_comments().reversed
        return self.current_comments

    # shared
    def get_events(self):
        """Returns all issue events."""
        return self.issue.get_events()

    # shared
    def resolve_labels(self, desired_label):
        """Resolves boilerplate the key labels to labels using an
        alias dict
        """
        for resolved_desired_label, aliases in self.alias_labels.iteritems():
            if desired_label in aliases:
                return resolved_desired_label
        return desired_label

    # shared
    def process_mutually_exlusive_labels(self, name=None):
        resolved_name = self.resolve_labels(name)
        if resolved_name in self.mutually_exclusive_labels:
            for label in self.desired_labels:
                resolved_label = self.resolve_labels(label)
                if resolved_label in self.mutually_exclusive_labels:
                    self.desired_labels.remove(label)

    # shared
    def add_desired_label(self, name=None):
        """Adds a label to the desired labels list"""
        if name and name not in self.desired_labels:
            self.process_mutually_exlusive_labels(name=name)
            self.desired_labels.append(name)

    # shared
    def add_desired_comment(self, boilerplate=None):
        """Adds a boilerplate key to the desired comments list"""
        if boilerplate and boilerplate not in self.desired_comments:
            self.desired_comments.append(boilerplate)

    # shared
    def add_label(self, label=None):
        """Adds a label to the PR using the GitHub API"""
        self.issue.add_to_labels(label)

    # shared
    def remove_label(self, label=None):
        """Removes a label from the PR using the GitHub API"""
        self.issue.remove_from_labels(label)

    # shared
    def add_comment(self, comment=None):
        """ Adds a comment to the PR using the GitHub API"""
        self.issue.create_comment(comment)


class Issue(BaseIssue):
    issue_type_name = 'Issue'
    alias_labels = ISSUE_ALIAS_LABELS
    mutually_exclusive_labels = ISSUE_MUTUALLY_EXCLUSIVE_LABELS
    manual_interaction_labels = ISSUE_MANUAL_INTERACTION_LABELS

    def __init__(self, repo, number=None, issue=None):
        super(PullRequest, self).__init__(repo=repo, number=number, issue=issue)
        # And issue is a issue, but a pull request has an issue (while also being an issue, but not
        #  in PyGithub
        self._issue = self.instance

    def __str__(self):
        lines = ["%s #%s: %s" % (self.issue_type_name, self.instance.number, (self.instance.title).encode('ascii','ignore'))]
        lines.append("Created at %s" % self.instance.created_at)
        lines.append("Updated at %s" % self.instance.updated_at)
        return '\n'.join(lines)

    def __repr__(self):
        issue_blurb = ''
        if self.instance:
            issue_blurb = ',issue=%s' % self.instance

        return '%s(repo=%s, number=%s%s)' % (self.__class__.__name__, self.repo, self.number, issue_blurb)

    def get_filenames(self):
        # TODO: do something clever (tracebacks, task ids in snippets, etc)
        return []


class PullRequest(BaseIssue):
    issue_type_name = 'Pull Request'
    alias_labels = PR_ALIAS_LABELS
    mutually_exclusive_labels = PR_MUTUALLY_EXCLUSIVE_LABELS
    manual_interaction_labels = PR_MANUAL_INTERACTION_LABELS

    def __init__(self, repo, number=None, issue=None):
        super(PullRequest, self).__init__(repo=repo, number=number, issue=issue)

    def _get_issue_type_instance(self, number):
        ret = self.repo.get_pull(number)
        log.debug("ret=%s", ret)
        return ret

    def get_filenames(self):
        """Returns all files related to this PR"""
        if not self.filenames:
            for pr_file in self.instance.get_files():
                self.filenames.append(pr_file.filename)
        return self.filenames

    def get_last_commit(self):
        """Returns last commit"""
        commits = self.instance.get_commits().reversed
        for commit in commits:
            return commit

    def get_build_status(self):
        """Return build status object"""
        last_commit = self.get_last_commit()
        if last_commit:
            build_statuses = last_commit.get_statuses()
            for build_status in build_statuses:
                return build_status
        return None

    def pr_contains_new_file(self):
        """Return True if PR contains new files"""
        for pr_file in self.instance.get_files():
            if pr_file.status == "added":
                return True
        return False

    def is_mergeable(self):
        """Return True if PR is mergeable"""
        while self.instance.mergeable_state == "unknown":
            print("Mergeable state is unknown, trying again...")
            time.sleep(1)
            self.instance = self.repo.get_pull(self.number)
            time.sleep(1)
        return self.instance.mergeable_state != "dirty"

    def get_base_ref(self):
        """Returns base ref of PR"""
        return self.instance.base.ref

    def is_a_wip(self):
        """Return True if PR start with [WIP] in title"""
        return (self.instance.title.startswith("[WIP]") or
              self.instance.title.startswith("WIP:") or
              self.instance.title.startswith("WIP "))

    def __str__(self):
        lines = ["%s #%s: %s" % (self.issue_type_name, self.instance.number, (self.instance.title).encode('ascii','ignore'))]
        lines.append("Created at %s" % self.instance.created_at)
        lines.append("Updated at %s" % self.instance.updated_at)
        return '\n'.join(lines)

    def __repr__(self):
        blurb = ''
        if self.instance:
            blurb = ',pr=%s' % self.instance

        return '%s(repo=%s, number=%s%s)' % (self.__class__.__name__, self.repo, self.number, blurb)


# TODO: create this once
class Maintainers(object):
    def __init__(self, maintainers_file=None):
        self.maintainers_file = maintainers_file
        self.maintainers = {}

    def for_issue(self, issue):
        """Return a set of maintainer user names of maintainers addressed by this issue.

        Checks issue.get_filenames(), and finds all the maintainers for all the paths."""
        filenames = issue.get_filenames()
        by_filename = self.for_paths(filenames)
        maintainers = set([])
        for maintainer_list in by_filename.values():
            maintainers.update(maintainer_list)

        return maintainers

    def for_path(self, path):
        module_maintainers = set([])
        module_maintainers.update(self.maintainers.get(path, []))
        return module_maintainers

    def for_paths(self, paths=None):
        paths = paths or []
        path_to_maintainers = defaultdict(set)
        for path in paths:
            path_to_maintainers[path].update(self.for_path(path))
        return path_to_maintainers

    # Seperate class
    def load(self):
        """Reads all known maintainers from files and their owner namespace"""
        self.maintainers = self._load(self.maintainers_file)

    def _load(self, filename):
        maintainers = {}
        with open(self.maintainers_file) as f:
            for line in f:
                owner_space = (line.split(': ')[0]).strip()
                maintainers_string = (line.split(': ')[-1]).strip()
                maintainers[owner_space] = maintainers_string.split(' ')
        return maintainers


# TODO: replace with a state machine
# shortterm goal is just pulling out all the logic that is different between
# TriageIssue/TriagePullRequest
class BaseProcessText(object):
    """Process list of comments for actionable info."""
    def __init__(self, issue=None, maintainers=None, team_members=None):
        # TODO: all of this is really info only needed in some comment processors
        self.extractors = []
        self.maintainers = maintainers or {}
        self.issue = issue
        self.module_maintainers = self.maintainers.for_issue(self.issue)
        self.team_members = team_members
        self.tracebacks = []
        self.versions = []

    # TODO: could just be module methods, or something this has-a
    def find_tracebacks(self, body):
        tbs = find_traceback.find_tracebacks(body)
        self.tracebacks.extend(tbs)
        log.debug('find_tracebacks: %s', tbs)
        if tbs:
            return True
        return False

    def find_py_files(self, body):
        py_file_re = r"""ansible(\S*?\.py)[\s+?:\\\"]"""
        matches = re.findall(py_file_re, body, flags=re.M)
        log.debug("find_py_files matches=%s", matches)
        if matches:
            print(body)
            return True
        return False

    def find_ansible_version(self, body):
        ansible_version_re = r"""\s+?ansible\s(\d+\.\d+\.\d+\.+.?)?"""
        # ansible_version_re = r"""ansible\s((\d+\.)+(\d+\.)+(\d+.*?))?"""
        matches = re.findall(ansible_version_re, body)
        log.debug("ansible_version matches=%s", matches)
        if matches:
            print(matches)
            self.versions.append(matches[0])
            return True
        return False


class ProcessComments(BaseProcessText):
    def process(self, comments):
        """ Processes PR comments for matching criteria for adding labels"""

        log.debug("--- START Processing Comments:")

        # TODO: check for tracebacks
        #       check for reference to other bug trackers

        desired_labels = []
        # split into methods ala self.add_labels_by_gitref
        for comment in comments:
            # TODO: accumulate actions
            desired_labels += self.process_comment(comment)

        log.debug("--- END Processing Comments")
        return desired_labels


class IssueProcessComments(ProcessComments):

    def process_comment(self, comment):
        # Is the last useful comment from a bot user?  Then we've got a
        # potential timeout case. Let's explore!
        desired_labels = []
        if comment.user.login in BOTLIST:

            log.debug("%s is in botlist: ", comment.user.login)

            today = datetime.today()
            time_delta = today - comment.created_at
            comment_days_old = time_delta.days

            log.debug("Days since last bot comment: %s",
                      comment_days_old)

            # TODO: something useful
            if comment_days_old > 14:
                pass

            log.debug("STATUS: no useful state change since last pass ( %s )", comment.user.login)
            return desired_labels

        if (comment.user.login in self.module_maintainers or
          comment.user.login.lower() in self.module_maintainers):
            log.debug("%s is module maintainer commented on %s.",
                      comment.user.login, comment.created_at)

            # Look for 'fixed in', 'regression' 'dupe'

            if "needs_info" in comment.body:
                log.debug("...said needs_info!")
                desired_labels.append("needs_info")

            if "close_me" in comment.body:
                log.debug("...said close_me!")
                desired_labels.append("pending_action_close_me")
                return desired_labels

        if comment.user.login == self.issue.get_submitter():
            log.debug("%s is Issue submitter commented on %s.",
                      comment.user.login, comment.created_at)

        if (comment.user.login not in BOTLIST and
          comment.user.login in self.team_members):

            log.debug("%s is a ansible member",
                      comment.user.login)

            if "needs_info" in comment.body:
                log.debug("...said needs_info!")
                desired_labels.append("needs_info")
                return desired_labels

        if self.find_tracebacks(comment.body):
            log.debug("found tracebacks in comment")
            desired_labels.append("traceback")

        if self.find_py_files(comment.body):
            log.debug("found py files in comment")

        log.debug("--- END Processing Comment")
        return desired_labels


class IssueProcessBody(BaseProcessText):
    def process(self):
        """ Processes PR comments for matching criteria for adding labels"""

        log.debug("--- START Processing Issue body:")

        # TODO: check for tracebacks
        #       check for reference to other bug trackers

        desired_labels = []
        # split into methods ala self.add_labels_by_gitref
        desired_labels += self.process_body(self.issue.instance.body)

        log.debug("--- END Processing issue body")
        return desired_labels

    def process_body(self, body):
        desired_labels = []
        if not body:
            log.debug("Issue has no description")
            return desired_labels

        # TODO: This could be generalized and just use a map of 'string_in_body':'type_of_label'
        if "Bug Report" in body:
            log.debug("Bug Report Issue")
            desired_labels.append("bug_report")

        if "Documentation Report" in body:
            log.debug("Docs Report")
            desired_labels.append("docs_report")

        if "Feature Idea" in body:
            log.debug("Feature Idea")
            desired_labels.append("feature_idea")

        if self.find_tracebacks(body):
            log.debug("Traceback found")
            desired_labels.append("traceback")

        if self.find_py_files(body):
            log.debug("python file found")

        if self.find_ansible_version(body):
            log.debug("Found ansible version")
            log.debug("Ansible version=%s", self.versions)

        # search for playbooks or yaml?
        # search for os versions
        # need a 'ansible --version'  parser... ;-<
        return desired_labels


class BaseTriageIssue(object):
    issue_type_class = Issue
    module_namespace_labels = MODULE_NAMESPACE_LABELS

    def __init__(self, verbose=None, github_user=None, github_pass=None,
                 github_token=None, github_repo=None, number=None,
                 start_at=None, always_pause=False, force=False,
                 dry_run=False, maintainers=None, team=None):
        self.verbose = verbose
        self.github_user = github_user
        self.github_pass = github_pass
        self.github_token = github_token
        self.github_repo = github_repo
        self.number = number
        self.start_at = start_at
        self.always_pause = always_pause
        self.force = force
        self.dry_run = dry_run

        self.issue = None

        self.team = team
        self.maintainers = maintainers
        self.tracebacks = []

        self.actions = {
            'newlabel': [],
            'unlabel':  [],
            'comments': [],
        }

    # shared
    def _connect(self):
        """Connects to GitHub's API"""
        # TODO: support getting token from os.environ or a config file.
        return Github(login_or_token=self.github_token or self.github_user,
                      password=self.github_pass)

    # shared
    def debug(self, msg=""):
        """Prints debug message if verbosity is given"""
        if self.verbose:
            print("Debug: " + msg)

    # shared
    def keep_current_main_labels(self):
        current_labels = self.issue.get_current_labels()
        for current_label in current_labels:
            if current_label in self.issue.mutually_exclusive_labels:
                self.issue.add_desired_label(name=current_label)

    def get_team_members(self):
        return self._connect().get_organization(self.team).get_members()

    # shared
    def is_team_member(self, login):
        user = self._connect().get_user(login)
        return self._connect().get_organization(self.team).has_in_members(user)

    def get_all(self, repo, direction=None):
        return repo.get_issues(direction=direction)

    def get_comments(self):
        return self.issue.get_comments()

    def get_events(self):
        log.debug('self.issue=%s', self.issue,)
        return self.issue.get_events()

    # shared
    def render_comment(self, boilerplate=None):
        """Renders templates into comments using the boilerplate as filename"""
        raise NotImplemented

    # shared
    def create_actions(self):
        """Creates actions from the desired label, unlabel and comment actions
        lists"""

        # create new label and comments action
        resolved_desired_pr_labels = []
        for desired_label in self.issue.desired_labels:

            # Most of the comments are only going to be added if we also add a
            # new label. So they are coupled. That is why we use the
            # boilerplate dict key as label and use an alias table containing
            # the real labels. This allows us to either use a real new label
            # without a comment or an label coupled with a comment. We check
            # if the label is a boilerplate dict key and get the real label
            # back or alternatively the label we gave as input
            # e.g. label: community_review_existing -> community_review
            # e.g. label: community_review -> community_review
            resolved_desired_label = self.issue.resolve_labels(desired_label)

            # If we didn't get back the same, it means we must also add a
            # comment for this label
            if desired_label != resolved_desired_label:

                # we cache for later use in unlabeling actions
                resolved_desired_pr_labels.append(resolved_desired_label)

                # We only add actions (newlabel, comments) if the label is
                # not already set
                if (resolved_desired_label not in
                        self.issue.get_current_labels()):
                    # Use the previous label as key for the boilerplate dict
                    self.issue.add_desired_comment(desired_label)
                    self.actions['newlabel'].append(resolved_desired_label)
            # it is a real label
            else:
                resolved_desired_pr_labels.append(desired_label)
                if (desired_label not in
                        self.issue.get_current_labels()):
                    self.actions['newlabel'].append(desired_label)
                    # how about a boilerplate with that label name?
                    if os.path.exists("templates/" + desired_label + ".j2"):
                        self.issue.add_desired_comment(desired_label)

        # unlabel action
        for current_label in self.issue.get_current_labels():

            # some labels we just ignore
            if current_label in IGNORE_LABELS:
                continue

            # now check if we need to unlabel
            if current_label not in resolved_desired_pr_labels:
                self.actions['unlabel'].append(current_label)

        for boilerplate in self.issue.desired_comments:
            # TODO/FIXME: pass in approriate maintainers
            comment = self.render_comment(boilerplate=boilerplate)
            self.debug(msg=comment)
            self.actions['comments'].append(comment)

    # shared
    def process_events(self):
        events = self.get_events()

        log.debug("start processing events")

        for event_index, event in enumerate(events):
            log.debug('%s: %s', event_index, github_helpers.IssueEventRepr(event))

        # log.debug('end of processing events')

    def process(self):
        """Processes the Issue"""
        # clear all actions
        self.actions = {
            'newlabel': [],
            'unlabel':  [],
            'comments': [],
        }

        # TODO: this is More or less a issue repr() so move it there
        # print some general infos about the PR to be processed

        self.keep_current_main_labels()
        self.add_desired_labels_by_namespace()

        self.add_labels()

        self.create_actions()

        self.report()
        return self.apply_actions()

    # TODO: repr/str
    def report(self):
        # Print the things we processed
        print("Submitter: %s" % self.issue.get_submitter())
        print("Maintainers: %s" % ', '.join(self.maintainers.for_issue(self.issue)))
        print("Current Labels: %s" %
              ', '.join(self.issue.current_labels))
        print("Actions: %s" % self.actions)

    def apply_actions(self):
        # TODO: maybe change to a functional style approach?
        #       iterate over the actions, filter away dry-run and skipped.
        #       Maybe provide a callback for the interactive prompting?
        #
        #       def confirm_execute_action_callback(self, action, label):
        #           if self.dry_run:
        #              return False
        #           if self.force:
        #              return True
        #           confirm_actions = ('newlabel', 'unlabel', 'comments')
        #           if label in confirm_actions:
        #              confirmed = self.do_action_prompt()
        #              return confirmed
        #
        if self.dry_run:
            print('--dry-run is set so skipping actions')
            log.debug("Would have run the following actions:")
            for action_type in self.actions:
                if self.actions[action_type]:
                    log.debug('action_type=%s action=%s', action_type, self.actions[action_type])
            return 0

        # TODO: make a Actions class with iter
        if (self.actions['newlabel'] or self.actions['unlabel'] or
                self.actions['comments']):
            if self.force:
                print("Running actions non-interactive as you forced.")
                self.execute_actions()
                return
            cont = raw_input("Take recommended actions (y/N/a)? ")
            if cont in ('a', 'A'):
                return 0
            if cont in ('Y', 'y'):
                self.execute_actions()
        elif self.always_pause:
            print("Skipping, but pause.")
            cont = raw_input("Continue (Y/n/a)? ")
            if cont in ('a', 'A', 'n', 'N'):
                return 0
        else:
            print("Skipping.")

# class Action(object):
# args may include the issue to run against, and needed params
#   def __init__(self, *args, **kwargs):
#   def execute(self):
#       action specific thing
#

    def execute_actions(self):
        """Turns the actions into API calls"""
        for unlabel in self.actions['unlabel']:
            self.debug(msg="API Call unlabel: " + unlabel)
            self.issue.remove_label(label=unlabel)
        for newlabel in self.actions['newlabel']:
            self.debug(msg="API Call newlabel: " + newlabel)
            self.issue.add_label(label=newlabel)
        for comment in self.actions['comments']:
            self.debug(msg="API Call comment: " + comment)
            self.issue.add_comment(comment=comment)

        # for action in self.actions:
        #    action.execute()

    def run(self):
        """Starts a triage run"""
        repo = self._connect().get_repo(self.github_repo)

        # TODO: a 'issue_builder' method that does the right thing likely
        #       makes more sense that self.issue_type_class pointing to the
        #       right associated issue class
        if self.number:
            log.info("Triaging issue/pr #%s", self.number)
            self.issue = self.issue_type_class(repo=repo,
                                               number=self.number)
            self.process()
        else:
            issues = self.get_all(repo, direction='desc')
            log.info("Triaging issues/prs starting at #%s", self.start_at)
            issues_to_triage = []
            for issue in issues:

                # FIXME: I flipped the order here. Need to handle either direction
                if self.start_at and issue.number < self.start_at:
                    break
                issues_to_triage.append(issue)

            log.debug('issues to triage=%s', issues_to_triage)
            for issue in issues_to_triage:
                log.info("Triaging issue/pr #%s", issue.number)
                self.issue = self.issue_type_class(repo=repo, issue=issue)
                self.process()


class TriageIssue(BaseTriageIssue):
    issue_type_class = Issue

    # Need to setup a IssueProcessComments in init.

    def add_labels(self):
        # process comments after labels
        comment_processor = IssueProcessComments(issue=self.issue,
                                                 maintainers=self.maintainers,
                                                 team_members=self.get_team_members())
        desired_labels = comment_processor.process(self.get_comments())

        desired_labels += self.add_labels_by_issue_text()

        for desired_label in desired_labels:
            self.issue.add_desired_label(name=desired_label)

        self.process_events()

        # TODO
        # self.add_desired_version_by_version_string()
        # self.add_desired_milestone_by_something_or_another()
        # self.add_desired_cli_label_by_reproducer_info()

    def add_desired_labels_by_namespace(self):
        """Adds labels regarding module namespaces.

        Note: For Issues, there are no filenames so this is a no op"""

        for filename in self.issue.get_filenames():
            namespace = filename.split('/')[0]
            for key, value in self.module_namespace_labels.iteritems():
                if key == namespace:
                    self.issue.add_desired_label(value)

    def add_labels_by_issue_text(self):
        """Adds labels by issue text"""
        body_processor = IssueProcessBody(issue=self.issue,
                                          maintainers=self.maintainers,
                                          team_members=self.get_team_members())
        desired_labels = body_processor.process()
        return desired_labels


class TriagePullRequest(BaseTriageIssue):
    issue_type_class = PullRequest

    def get_all(self, repo):
        return repo.get_pulls()

    # NOTE: add_labels is a misnomer, really means 'check the state, figure out what we need to do, build the
    #       list of actions, etc.
    def add_labels(self):
        self.process_events()

        self.add_desired_labels_by_maintainers()
        self.add_desired_labels_by_gitref()
        # process comments after labels
        self.process_comments()
        self.add_desired_labels_for_not_mergeable()
        self.add_desired_label_by_build_state()
        self.add_labels_by_issue_text()

    def add_desired_labels_by_namespace(self):
        """Adds labels regarding module namespaces.

        Note: For Issues, there are no filenames so this is a no op"""

        for filename in self.issue.get_filenames():
            namespace = filename.split('/')[0]
            for key, value in self.module_namespace_labels.iteritems():
                if key == namespace:
                    self.issue.add_desired_label(value)

    def add_labels_by_issue_text(self):
        """Adds labels by issue type"""
        body = self.issue.instance.body

        # TODO: similar to process_comment, this takes the issue body
        #       and figures out current state and the actions needed to
        #       get it to the desired state.
        if not body:
            self.debug(msg="PR has no description")
            return

        if self.issue.is_a_wip():
            self.debug(msg="PR is a work-in-progress")
            self.issue.add_desired_label(name="work_in_progress")

        if ("New Module Pull Request" in body or
          "new_plugin" in self.issue.desired_labels):
            self.debug(msg="New Module Pull Request")
            return

        if "Bugfix Pull Request" in body:
            self.debug(msg="Bugfix Pull Request")
            self.issue.add_desired_label(name="bugfix_pull_request")

        if "Docs Pull Request" in body:
            self.debug(msg="Docs Pull Request")
            self.issue.add_desired_label(name="docs_pull_request")

        if "Feature Pull Request" in body:
            self.debug(msg="Feature Pull Request")
            self.issue.add_desired_label(name="feature_pull_request")

    def add_desired_labels_for_not_mergeable(self):
        """Adds labels for not mergeable conditions"""
        if not self.issue.is_mergeable():
            self.debug(msg="PR is not mergeable")
            self.issue.add_desired_label(name="needs_revision_not_mergeable")
        else:
            self.debug(msg="PR is mergeable")

    def add_desired_labels_by_maintainers(self):
        """Adds labels regarding maintainer infos"""
        module_maintainers = self.maintainers.for_issue(self.issue)
        pr_contains_new_file = self.issue.pr_contains_new_file()

        if pr_contains_new_file:
            self.debug(msg="plugin is new")
            self.issue.add_desired_label(name="new_plugin")

        if "shipit" in self.issue.get_current_labels():
            self.debug(msg="shipit labeled, skipping maintainer")
            return

        if "needs_info" in self.issue.get_current_labels():
            self.debug(msg="needs info labeled, skipping maintainer")
            return

        if "needs_revision" in self.issue.get_current_labels():
            self.debug(msg="needs revision labeled, skipping maintainer")
            return

        if "core_review" in self.issue.get_current_labels():
            self.debug(msg="Forced core review, skipping maintainer")
            return

        if "ansible" in module_maintainers:
            self.debug(msg="ansible in module maintainers")
            self.issue.add_desired_label(name="core_review_existing")
            return

        if (self.issue.get_submitter() in module_maintainers or
          self.issue.get_submitter().lower() in module_maintainers):
            self.debug(msg="plugin by owner, community review as owner_pr")
            self.issue.add_desired_label(name="owner_pr")
            self.issue.add_desired_label(name="community_review_owner_pr")
            return

        if not module_maintainers and not pr_contains_new_file:
            self.debug(msg="unknown maintainer.")
            self.issue.add_desired_label(name="pending_maintainer_unknown")
            return

        if not module_maintainers and pr_contains_new_file:
            self.debug(msg="New plugin, no module maintainer yet")
            self.issue.add_desired_label(name="community_review_new")
        else:
            self.debug(msg="existing plugin modified, module maintainer "
                           "should review")
            self.issue.add_desired_label(
                name="community_review_existing"
            )

    def add_desired_labels_by_gitref(self):
        """Adds labels regarding gitref"""
        if "stable" in self.issue.get_base_ref():
            self.debug(msg="backport requested")
            self.issue.add_desired_label(name="core_review")
            self.issue.add_desired_label(name="backport")

    def add_desired_label_by_build_state(self):
        """Adds label regarding build state of last commit"""
        build_status = self.issue.get_build_status()
        if build_status:
            self.debug(msg="Build state is %s" % build_status.state)
            if build_status.state == "failure":
                self.issue.add_desired_label(name="needs_revision")
        else:
            self.debug(msg="No build state")

    def process_comments(self):
        """ Processes PR comments for matching criteria for adding labels"""
        module_maintainers = self.maintainers.for_issue(self.issue)
        comments = self.get_comments()

        self.debug(msg="--- START Processing Comments:")

        for comment in comments:

            # TODO: move all of this into a process_comment method and
            #       quite possibly it's own class. Maybe multiple classes
            #       The classes would take a comment and return a list of actions
            #       to perform. Maybe even split 'figure out current state' and 'build
            #       the actions to move to the next state'.
            # Is the last useful comment from a bot user?  Then we've got a
            # potential timeout case. Let's explore!
            if comment.user.login in BOTLIST:

                self.debug(msg="%s is in botlist: " % comment.user.login)

                today = datetime.today()
                time_delta = today - comment.created_at
                comment_days_old = time_delta.days

                self.debug(msg="Days since last bot comment: %s" %
                           comment_days_old)

                if comment_days_old > 14:
                    # Kind of want a state machine here, and timers that can force transitions
                    pr_labels = self.issue.desired_labels

                    if "core_review" in pr_labels:
                        self.debug(msg="has core_review")
                        break

                    if "pending" not in comment.body:
                        if self.issue.is_labeled_for_interaction():
                            self.issue.add_desired_comment(
                                boilerplate="submitter_first_warning"
                            )
                            self.debug(msg="submitter_first_warning")
                            break
                        if ("community_review" in pr_labels and not
                                self.issue.pr_contains_new_file()):
                            self.debug(msg="maintainer_first_warning")
                            self.issue.add_desired_comment(
                                boilerplate="maintainer_first_warning"
                            )
                            break

                    # pending in comment.body
                    else:
                        if self.issue.is_labeled_for_interaction():
                            self.debug(msg="submitter_second_warning")
                            self.issue.add_desired_comment(
                                boilerplate="submitter_second_warning"
                            )
                            self.issue.add_desired_label(
                                name="pending_action"
                            )
                            break
                        if ("community_review" in pr_labels and
                          "new_plugin" not in pr_labels):
                            self.debug(msg="maintainer_second_warning")
                            self.issue.add_desired_comment(
                                boilerplate="maintainer_second_warning"
                            )
                            self.issue.add_desired_label(
                                name="pending_action"
                            )
                            break
                self.debug(msg="STATUS: no useful state change since last pass"
                           "( %s )" % comment.user.login)
                break

            if (comment.user.login in module_maintainers or
              comment.user.login.lower() in module_maintainers):
                self.debug(msg="%s is module maintainer commented on %s." %
                           (comment.user.login, comment.created_at))

                if ("shipit" in comment.body or "+1" in comment.body or
                  "LGTM" in comment.body):
                    self.debug(msg="...said shipit!")
                    # if maintainer was the submitter:
                    if comment.user.login == self.issue.get_submitter():
                        self.issue.add_desired_label(name="shipit_owner_pr")
                    else:
                        self.issue.add_desired_label(name="shipit")
                    break

                elif "needs_revision" in comment.body:
                    self.debug(msg="...said needs_revision!")
                    self.issue.add_desired_label(name="needs_revision")
                    break

                elif "needs_info" in comment.body:
                    self.debug(msg="...said needs_info!")
                    self.issue.add_desired_label(name="needs_info")

                elif "close_me" in comment.body:
                    self.debug(msg="...said close_me!")
                    self.issue.add_desired_label(name="pending_action_close_me")
                    break

            if comment.user.login == self.issue.get_submitter():
                self.debug(msg="%s is PR submitter commented on %s." %
                           (comment.user.login, comment.created_at))
                if "ready_for_review" in comment.body:
                    self.debug(msg="...ready for review!")
                    if "ansible" in module_maintainers:
                        self.debug(msg="core does the review!")
                        self.issue.add_desired_label(
                            name="core_review_existing"
                        )
                    elif not module_maintainers:
                        self.debug(msg="community does the review!")
                        self.issue.add_desired_label(
                            name="community_review_new"
                        )
                    else:
                        self.debug(msg="community does the review but has "
                                   "maintainer")
                        self.issue.add_desired_label(
                            name="community_review_existing"
                        )
                    break

            team_members = self.get_team_members()

            if (comment.user.login not in BOTLIST and
              comment.user.login in team_members):

                self.debug(msg="%s is a ansible member" % comment.user.login)

                if ("shipit" in comment.body or "+1" in comment.body or
                  "LGTM" in comment.body):
                    self.debug(msg="...said shipit!")
                    self.issue.add_desired_label(name="shipit")
                    break

                elif "needs_revision" in comment.body:
                    self.debug(msg="...said needs_revision!")
                    self.issue.add_desired_label(name="needs_revision")
                    break

                elif "needs_info" in comment.body:
                    self.debug(msg="...said needs_info!")
                    self.issue.add_desired_label(name="needs_info")
                    break

        self.debug(msg="--- END Processing Comments")

    def render_comment(self, maintainers=None, boilerplate=None):
        """Renders templates into comments using the boilerplate as filename"""
        maintainers = maintainers or ['ansible/core']

        submitter = self.issue.get_submitter()

        template = environment.get_template('%s.j2' % boilerplate)
        comment = template.render(maintainer=maintainers, submitter=submitter)
        return comment

def main():
    parser = argparse.ArgumentParser(description="Triage various PR queues "
                                                 "for Ansible. (NOTE: only "
                                                 "useful if you have commit "
                                                 "access to the repo in "
                                                 "question.)")
    parser.add_argument("repo", type=str, choices=['ansible', 'core', 'extras'],
                        help="Repo to be triaged")
    parser.add_argument("--gh-user", "-u", type=str,
                        help="Github username or token of triager")
    parser.add_argument("--gh-pass", "-P", type=str,
                        help="Github password of triager")
    parser.add_argument("--gh-token", "-T", type=str,
                        help="Github token of triager")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Do not ask questions")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Debug output")
    parser.add_argument("--pause", "-p", action="store_true",
                        help="Always pause between PRs")
    parser.add_argument("--pr", type=int,
                        help="Triage only the specified pr")
    parser.add_argument("--prs", action="store_true",
                        default=False,
                        help="Triage pull requests.")
    parser.add_argument("--issue", type=int,
                        help="Triage only the specified issue")
    parser.add_argument("--issues", action="store_true",
                        default=False,
                        help="Triage issues.")
    parser.add_argument("--start-at", type=int,
                        help="Start triage at the specified pr")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Don't change any pull requests.")
    args = parser.parse_args()

    team = TEAM
    logging.getLogger('github.Requester').setLevel(logging.INFO)

    if args.pr and args.start_at:
        raise TriageError("Error: Mutually exclusive: --start-at and --pr")

    if args.force and args.pause:
        raise TriageError("Error: Mutually exclusive: --force and --pause")

    log.debug('args.prs=%s', args.prs)
    log.debug('args.issue=%s', args.issues)

    # shelf = shelve.open(filename='shelve.data', flag='c', protocol=2)

    maintainers = Maintainers(maintainers_file=MAINTAINERS_FILES[args.repo])
    maintainers.load()

    # expand the repo nickname
    repo_slug = REPO_NICKNAMES[args.repo]

    if args.pr or args.prs:
        triage = TriagePullRequest(
            verbose=args.verbose,
            github_user=args.gh_user,
            github_pass=args.gh_pass,
            github_token=args.gh_token,
            github_repo=repo_slug,
            number=args.pr,
            start_at=args.start_at,
            always_pause=args.pause,
            force=args.force,
            dry_run=args.dry_run,
            maintainers=maintainers,
            team=team
        )

    if args.issue or args.issues:
        triage = TriageIssue(
            verbose=args.verbose,
            github_user=args.gh_user,
            github_pass=args.gh_pass,
            github_token=args.gh_token,
            github_repo=repo_slug,
            number=args.issue,
            start_at=args.start_at,
            always_pause=args.pause,
            force=args.force,
            dry_run=args.dry_run,
            maintainers=maintainers,
            team=team
        )

    triage.run()

if __name__ == "__main__":
    try:
        sys.exit(main())
    except TriageError as e:
        log.exception(e)
        sys.exit(1)
