#!/usr/bin/python

# THIS IS NOW DEPRECATED
# Use triage.py instead
# (In fact, I should throw this out)

# TODOs:
#   * Fix handling of multi-file PRs (and decide policy)
#   * Simplify to only the useful output

# Useful! https://developer.github.com/v3/pulls/
# Useful! https://developer.github.com/v3/issues/comments/

import argparse
import logging
import json
import signal
import sys
import time

import requests
import github

# Here's a nasty hack to get around the occasional ssl handshake
# timeout.  Thanks, ssl!

BOT_USER_NAMES = ['gregdek','robynbergeron']
GITHUB_API_URL = 'https://api.github.com/'
DEBUG_LOG_FORMAT = "%(asctime)s [%(name)s %(levelname)s] (%(process)d):%(funcName)s:%(lineno)d - %(message)s"

logging.basicConfig(level=logging.DEBUG,
                    format=DEBUG_LOG_FORMAT)

log = logging.getLogger(__name__)

def handler(signum, frame):
    print 'Signal handler called with signal', signum
    raise IOError("Oops, SSL sucks!")

# Set up the signal handler
signal.signal(signal.SIGALRM, handler)

class RepoUrl(object):
    def __init__(self, gh_api_url=None, gh_org=None, gh_repo=None, state=None, start_at=None):
        self.start_at = start_at
        self.state = state
        self.gh_repo = gh_repo or 'ansible-modules-extras'
        self.gh_org = gh_org or self.gh_org
        self.gh_api_url = gh_api_url or GITHUB_API_URL
        self.url = '%(base_url)s/%(gh_org)s/%(gh_repo)s/pulls' % {'base_url': GITHUB_API_URL,
                                                                  'gh_org': self.gh_org,
                                                                  'gh_repo': self.gh_repo}
        self.url_params = {'state': self.state,
                           'start_at': self.start_at}


class PullRequestParam(object):
    def __init__(self):
        self.state = 'open'
        self.page = 1


class PullRequest(object):
    def __init__(self, gh_org, gh_repo, pr):
        self.gh_org = gh_org
        self.gh_repo = gh_repo
        self.pr = pr
        self.pr_url = '%(base_url)s/%(gh_org)s/%(gh_repo)s/pulls' % {'base_url': GITHUB_API_URL,
                                                                     'gh_org': self.gh_org,
                                                                     'gh_repo': self.gh_repo}

class CliOptions(object):
    """All of the options that are settable by the Cli.

    Used by argparse to set options on via the namespace= arg."""
    ghuser = None
    ghpass = None
    ghtoken = None
    ghrepo = None
    ghorg = 'ansible'
    verbose = False
    debug = False
    pause = True
    pr = None
    # FIXME: explain why 9999
    start_at = 9999
    state = 'open'
    dry_run = False


class Cli(object):
    def __init__(self):
        self.options = CliOptions()
        self.arg_parser = argparse.ArgumentParser(description=
                                                  'Triage various PR queues for Ansible. (NOTE: only useful if you have commit access to the repo in question.)')
        # TODO: make the triage.py and prbot.py args match
        self.arg_parser.add_argument("--ghuser",
                                     type=str,
                                     help="Github username of triager")
        self.arg_parser.add_argument("--ghpass",
                                     type=str,
                                     help="Github password of triager")
        self.arg_parser.add_argument("--ghrepo",
                                     type=str,
                                     choices=['core','extras'],
                                     help="Repo to be triaged")
        self.arg_parser.add_argument("--ghtoken", "-T",
                                     type=str,
                                     help="Github token of triager")
        self.arg_parser.add_argument('--verbose', '-v',
                                     action='store_true',
                                     default=False,
                                     help="Verbose output")
        self.arg_parser.add_argument('--debug', '-d',
                                     action='store_true',
                                     help="Debug output")
        self.arg_parser.add_argument('--pause', '-p', action='store_true',
                                     dest="pause_before_actions",
                                     help="Always pause between PRs")
        self.arg_parser.add_argument('--pr',
                                     type=str,
                                     help="Triage only the specified pr")
        self.arg_parser.add_argument('--startat',
                                     type=str,
                                     help="Start triage at the specified pr")
        self.arg_parser.add_argument('--dry-run',
                                     '-n',
                                     action='store_true',
                                     dest="dry_run",
                                     default=False,
                                     help="Only do 'read-only' operations and show the actions that would be run")
        self.args = self.arg_parser.parse_args(namespace=self.options)

class BotUserNames(object):
    """The user names that may be used by the bot."""
    def __init__(self, user_names=None):
        self.user_names = user_names or []

boilerplate = {
    'shipit': "Thanks again to @{s} for this PR, and thanks @{m} for reviewing. Marking for inclusion.",
    'backport': "Thanks @{s}. All backport requests must be reviewed by the core team, and this can take time. We appreciate your patience.",
    'community_review_existing': 'Thanks @{s}. @{m} please review according to guidelines (http://docs.ansible.com/ansible/developing_modules.html#module-checklist) and comment with text \'shipit\' or \'needs_revision\' as appropriate.',
    'core_review_existing': 'Thanks @{s} for this PR. This module is maintained by the Ansible core team, so it can take a while for patches to be reviewed. Thanks for your patience.',
    'community_review_new': 'Thanks @{s} for this new module. When this module receives \'shipit\' comments from two community members and any \'needs_revision\' comments have been resolved, we will mark for inclusion.',
    'shipit_owner_pr': 'Thanks @{s}. Since you are a maintainer of this module, we are marking this PR for inclusion.',
    'needs_rebase': 'Thanks @{s} for this PR. Unfortunately, it is not mergeable in its current state due to merge conflicts. Please rebase your PR. When you are done, please comment with text \'ready_for_review\' and we will put this PR back into review.',
    'needs_revision': 'Thanks @{s} for this PR. A maintainer of this module has asked for revisions to this PR. Please make the suggested revisions. When you are done, please comment with text \'ready_for_review\' and we will put this PR back into review.',
    'maintainer_first_warning': '@{m} This change is still pending your review; do you have time to take a look and comment? Please comment with text \'shipit\' or \'needs_revision\' as appropriate.',
    'maintainer_second_warning': '@{m} still waiting on your review.  Please comment with text \'shipit\' or \'needs_revision\' as appropriate. If we don\'t hear from you within 14 days, we will start to look for additional maintainers for this module.',
    'submitter_first_warning': '@{s} A friendly reminder: this pull request has been marked as needing your action. If you still believe that this PR applies, and you intend to address the issues with this PR, just let us know in the PR itself and we will keep it open pending your changes.',
    'submitter_second_warning': '@{s} Another friendly reminder: this pull request has been marked as needing your action. If you still believe that this PR applies, and you intend to address the issues with this PR, just let us know in the PR itself and we will keep it open. If we don\'t hear from you within another 14 days, we will close this pull request.'
}

class PrBot(object):
    def __init__(self, options=None):
        # FIXME: decouple form CliOptions, aka, pass in a ton of args
        self.options = options or CliOptions()
        self.verbose = self.options.verbose
        self.debug = self.options.debug
        self.github_token = self.options.ghtoken
        self.github_user = self.options.ghuser
        self.github_pass = self.options.ghpass
        self.github = self._connect()
        self.log = logging.getLogger(__name__ + '.' + self.__class__.__name__)

    def _connect(self):
        """Connects to GitHub's API"""
        #return github.Github(login_or_token=self.github_token or self.github_user,
        #                     password=self.github_pass)
        return github.Github(login_or_token=self.github_token,
                      base_url='https://api.github.com')

    def triage_repo(self, github_reponame, state=None):
        self.log.debug('repo=%s, state=%s', github_reponame, state)
        github_repo = self.github.get_repo(github_reponame)
        self.log.debug('github_repo=%s', github_repo)
        state = state or 'open'
        pull_requests = github_repo.get_pulls(state=state)
        self.log.debug('pull_requests=%s', pull_requests)
        return self.triage_pull_requests(pull_requests=pull_requests)

    def triage_pull_requests(self, pull_requests):
        for pull_request in pull_requests:
            self.triage_pull_request(pull_request)

    # TODO: triage(pull_requests=None)
    def triage_pull_request(self, pull_request):
        """Take a PR url and returns defailed PR data from the api."""

        # TODO: replace with logging
        verbose = self.verbose
        debug = self.debug

        if self.debug:
            debugfileid = '/tmp/pull_request-' + str(pull_request['number'])
            print "DEBUG JSON TO: ", debugfileid
            debugfile = open(debugfileid, 'w')
            print >>debugfile, json.dumps(pull_request, ensure_ascii=True, indent=4, separators=(',', ': '))
            debugfile.close()

        #----------------------------------------------------------------------------
        # Initialize an empty local list of PR labels; we'll need it later.
        #----------------------------------------------------------------------------
        pr_labels = []

        #----------------------------------------------------------------------------
        # Pull the list of files being edited so we can find maintainers.
        # (Warn if there's more than one; we can't handle that case yet.)
        #----------------------------------------------------------------------------
        # Now pull the text of the diff.

        signal.alarm(5)
        while True:
            try:
                diff = requests.get(pull_request['diff_url'], auth=(ghuser,ghpass), verify=False).text
                break # because pull worked
            except:
                print "Timeout, retrying..."
        signal.alarm(0)

        if debug:
            debugfileid = '/tmp/diff-' + str(pull_request['number'])
            print "DEBUG DIFF TO: ", debugfileid
            debugfile = open(debugfileid, 'w')
            print >>debugfile, json.dumps(diff, ensure_ascii=True, indent=4, separators=(',', ': '))
            debugfile.close()

        # Grep the diff for affected files.
        pr_contains_new_file = ''
        pyfilecounter = 0
        for line in diff.split('\n'):
            #------------------------------------------------------------------------
            # If there's a line that contains "--- /dev/null" then we know this PR
            # contains a new file. Set that so we can handle properly later.
            #------------------------------------------------------------------------
            if '--- /dev/null' in line:
                pr_contains_new_file = 'True'
            #------------------------------------------------------------------------
            # If there's a "diff git", that contains the file name being edited.
            #------------------------------------------------------------------------
            if 'diff --git' in line:
                # This split gives us the file name.
                pr_filename = line.split(' b/')[1]
                # Another split gives us the extension.
                pr_fileextension = pr_filename.split('.')[-1]
                if pr_fileextension == 'py':
                    pyfilecounter += 1
        # if multiple .py files are included in the diff, complain.
        if pyfilecounter == 0:
            if verbose:
                print "  WARN: no python files in this PR"
        if pyfilecounter > 1:
            if verbose:
                print "  WARN: multiple python files in this PR"
        if verbose:
            print "  Filename:", pr_filename

        #----------------------------------------------------------------------------
        # Look up the files in the local DB to see who maintains them.
        # (Warn if there's more than one; we can't handle that case yet.)
        #----------------------------------------------------------------------------
        pr_maintainers_list = []
        if ghrepo == "core":
            f = open('MAINTAINERS-CORE.txt')
        elif ghrepo == "extras":
            f = open('MAINTAINERS-EXTRAS.txt')
        for line in f:
            owner_space = (line.split(': ')[0]).strip()
            if owner_space in pr_filename:
                maintainers_string = (line.split(': ')[-1]).strip()
                for maintainer in maintainers_string.split(' '):
                    pr_maintainers_list.append(maintainer)
        f.close()

        pr_maintainers = ' '.join(pr_maintainers_list)

        #----------------------------------------------------------------------------
        # Pull the list of labels on this PR and shove them into pr_labels.
        #----------------------------------------------------------------------------
        signal.alarm(5)
        while True:
            try:
                issue = requests.get(pull_request['issue_url'], auth=(ghuser,ghpass)).json()
                break # because pull worked
            except:
                print "Timeout, retrying..."
        signal.alarm(0)

        # Print labels for now, so we know whether we're doing the right things
        for label in issue['labels']:
            pr_labels.append(label['name'])

        #----------------------------------------------------------------------------
        # Get and print key info about the PR.
        #----------------------------------------------------------------------------
        print " "
        print "****************************************************"
        print pull_request['number'], '---', pull_request['title']
        pr_submitter = pull_request['user']['login']
        print "  Labels: ", pr_labels
        print "  Submitter: ", pr_submitter
        print "  Maintainer(s): ", pr_maintainers
        print "  Filename(s): ", pr_filename
        print " "
        if verbose:
            print pull_request['body']

        #----------------------------------------------------------------------------
        # NOW: We have everything we need to do actual triage. In triage, we
        # assess the actions that need to be taken and push them into a list.
        # Get our comments, and set our empty actions list.
        #----------------------------------------------------------------------------
        signal.alarm(5)
        while True:
            try:
                comments = requests.get(pull_request['comments_url'], auth=(ghuser,ghpass), verify=False)
                break # because pull worked
            except:
                print "Timeout, retrying..."
        signal.alarm(0)

        actions = []

        #----------------------------------------------------------------------------
        # Kill all P3-P5 tags, every time. No more low priority tags.
        #----------------------------------------------------------------------------
        if ('P3') in pr_labels:
            actions.append("unlabel: P3")
        if ('P4') in pr_labels:
            actions.append("unlabel: P4")
        if ('P5') in pr_labels:
            actions.append("unlabel: P5")

        #----------------------------------------------------------------------------
        # Now, we handle the "no triaged labels" case: i.e. if none of the
        # following labels are present: community_review, core_review, needs_revision,
        # needs_rebase, shipit.
        #----------------------------------------------------------------------------

        if (('community_review' not in pr_labels)
        and ('core_review' not in pr_labels)
        and ('needs_revision' not in pr_labels)
        and ('needs_info' not in pr_labels)
        and ('needs_rebase' not in pr_labels)
        and ('shipit' not in pr_labels)):
            if ('stable' in pull_request['base']['ref']):
                actions.append("newlabel: core_review")
                actions.append("newlabel: backport")
                actions.append("boilerplate: backport")
            elif ('ansible' in pr_maintainers):
                actions.append("newlabel: core_review")
                actions.append("boilerplate: core_review_existing")
            elif (pr_maintainers == '') and (pr_contains_new_file):
                actions.append("newlabel: community_review")
                actions.append("newlabel: new_plugin")
                actions.append("boilerplate: community_review_new")
            elif (pr_maintainers == '') and (not pr_contains_new_file):
                print "FATAL: existing file without reviewer found! Please add to CONTRIBUTORS file."
                sys.exit(1)
            elif (pr_submitter in pr_maintainers):
                actions.append("newlabel: shipit")
                actions.append("newlabel: owner_pr")
                actions.append("boilerplate: shipit_owner_pr")
            else:
                actions.append("newlabel: community_review")
                actions.append("boilerplate: community_review_existing")

        #------------------------------------------------------------------------
        # Does this PR need to be (newly) rebased? If so, label and boilerplate.
        #------------------------------------------------------------------------
        if (pull_request['mergeable'] == False):
            print "WARN: not mergeable!"
            if ('needs_rebase' not in pr_labels):
                actions.append("newlabel: needs_rebase")
                actions.append("unlabel: community_review")
                actions.append("unlabel: core_review")
                actions.append("boilerplate: needs_rebase")

        #------------------------------------------------------------------------
        # Has PR been rebased at our request? If so, remove needs_rebase
        # label and put into the appropriate review state.
        #------------------------------------------------------------------------
        if ((pull_request['mergeable'] == True)
        and ('needs_rebase' in pr_labels)):
            actions.append("unlabel: needs_rebase")
            if ('ansible' in pr_maintainers):
                actions.append("newlabel: core_review")
                actions.append("boilerplate: core_review_existing")
            elif (pr_maintainers == '') and (pr_contains_new_file):
                actions.append("newlabel: community_review")
                actions.append("boilerplate: community_review_new")
            else:
                actions.append("newlabel: community_review")
                actions.append("boilerplate: community_review_existing")

        #----------------------------------------------------------------------------
        # Now let's add filename-based labels: cloud, windows, networking.
        # label and put into the appropriate review state.
        #----------------------------------------------------------------------------
        if (pr_filename.split('/')[0] == 'cloud') and ('cloud' not in pr_labels):
            actions.append("newlabel: cloud")
        if (pr_filename.split('/')[0] == 'network') and ('networking' not in pr_labels):
            actions.append("newlabel: networking")
        if (pr_filename.split('/')[0] == 'windows') and ('windows' not in pr_labels):
            actions.append("newlabel: windows")

        #----------------------------------------------------------------------------
        # OK, now we start walking through comment-based actions, and push whatever
        # we find into the action list.
        #
        # NOTE: we walk through comments MOST RECENT FIRST. Whenever we find a
        # meaningful state change from the comments, we break; thus, we are always
        # acting on what we perceive to be the most recent meaningful comment, and
        # we ignore all older comments.
        #----------------------------------------------------------------------------
        for comment in reversed(comments.json()):

            if verbose:
                print " "
                print "==========>  Comment at ", comment['created_at'], " from: ", comment['user']['login']
                print comment['body']

            #------------------------------------------------------------------------
            # Is the last useful comment from a bot user?  Then we've got a potential
            # timeout case.  Let's explore!
            #------------------------------------------------------------------------
            botlist = BotUserNames(user_names=BOT_USER_NAMES)
            if (comment['user']['login'] in botlist):

                #--------------------------------------------------------------------
                # Let's figure out how old this comment is, exactly.
                #--------------------------------------------------------------------
                comment_time = time.mktime((time.strptime(comment['created_at'], "%Y-%m-%dT%H:%M:%SZ")))
                comment_days_old = (time.time()-comment_time)/86400

                #--------------------------------------------------------------------
                # Is it more than 14 days old? That kinda sucks; we should do
                # something about it!
                #--------------------------------------------------------------------

                if comment_days_old > 14:

                    #----------------------------------------------------------------
                    # We know we've hit a timeout threshhold. Which one?
                    #----------------------------------------------------------------

                    #----------------------------------------------------------------
                    # If it's in core review, we just leave it be and break.
                    # (We'll set a different threshhold for core_review PRs
                    # in the future.)
                    #----------------------------------------------------------------
                    if 'core_review' in pr_labels:
                        break

                    #----------------------------------------------------------------
                    # If it's in needs_review or needs_rebase and no previous
                    # warnings have been issued, warn submitter and break.
                    #----------------------------------------------------------------
                    elif (('pending' not in comment['body'])
                    and (('needs_revision' in pr_labels) or ('needs_rebase' in pr_labels))):
                        actions.append("boilerplate: submitter_first_warning")
                        break

                    #----------------------------------------------------------------
                    # If it's in community_review and no previous # warnings have
                    # been issued, and it's not a new module (we let new modules
                    # stay in review indefinitely), warn maintainer and break.
                    #----------------------------------------------------------------
                    elif (('pending' not in comment['body'])
                    and ('community_review' in pr_labels)
                    and ('new_plugin' not in pr_labels)):
                        actions.append("boilerplate: maintainer_first_warning")
                        break

                    #----------------------------------------------------------------
                    # If it's in needs_revision or needs_rebase and a previous
                    # warning has been issued, place in pending_action, give the
                    # submitter a second warning, and break.
                    #----------------------------------------------------------------
                    elif (('pending' in comment['body'])
                    and (('needs_revision' in pr_labels) or ('needs_rebase' in pr_labels))):
                        actions.append("boilerplate: submitter_second_warning")
                        actions.append("label: pending_action")
                        break

                    #----------------------------------------------------------------
                    # If it's in community_review, not new_plugin, and a previous
                    # warning has been issued, place in pending_action, give the
                    # maintainer a second warning, and break.
                    #----------------------------------------------------------------
                    elif (('pending' in comment['body'])
                    and ('community_review' in pr_labels)
                    and ('new_plugin' not in pr_labels)):
                        actions.append("boilerplate: maintainer_second_warning")
                        actions.append("label: pending_action")
                        break

                if verbose:
                    print "  STATUS: no useful state change since last pass (", comment['user']['login'], ")"
                    print "  Days since last bot comment: ", comment_days_old

                break

            #------------------------------------------------------------------------
            # Has maintainer said 'shipit'? Then label/boilerplate/break.
            #------------------------------------------------------------------------
            if ((comment['user']['login'] in pr_maintainers)
            and ('shipit' in comment['body'])):
                actions.append("unlabel: community_review")
                actions.append("unlabel: core_review")
                actions.append("unlabel: needs_info")
                actions.append("unlabel: needs_revision")
                actions.append("unlabel: pending_action")
                actions.append("newlabel: shipit")
                actions.append("boilerplate: shipit")
                break

            #------------------------------------------------------------------------
            # Has maintainer said 'needs_revision'? Then label/boilerplate/break.
            #------------------------------------------------------------------------
            if ((comment['user']['login'] in pr_maintainers)
            and ('needs_revision' in comment['body'])):
                actions.append("unlabel: community_review")
                actions.append("unlabel: core_review")
                actions.append("unlabel: needs_info")
                actions.append("unlabel: shipit")
                actions.append("unlabel: pending_action")
                actions.append("newlabel: needs_revision")
                actions.append("boilerplate: needs_revision")
                break

            #------------------------------------------------------------------------
            # Has submitter said 'ready_for_review'? Then label/boilerplate/break.
            #------------------------------------------------------------------------
            if ((comment['user']['login'] == pr_submitter)
            and ('ready_for_review' in comment['body'])):
                actions.append("unlabel: needs_revision")
                actions.append("unlabel: needs_info")
                actions.append("unlabel: pending_action")
                if ('ansible' in pr_maintainers):
                    actions.append("newlabel: core_review")
                    actions.append("boilerplate: core_review_existing")
                elif (pr_maintainers == ''):
                    actions.append("newlabel: community_review")
                    actions.append("boilerplate: community_review_new")
                else:
                    actions.append("newlabel: community_review")
                    actions.append("boilerplate: community_review_existing")
                break

            #------------------------------------------------------------------------
            # Have submitter or maintainer said something else? Then they're
            # likely discussing issues with the PR; that makes this comment
            # "useful", so we'll break here so as not to trigger the timeout
            # workflow.
            #------------------------------------------------------------------------
            if ((comment['user']['login'] in pr_maintainers)
            or (comment['user']['login'] == pr_submitter)):
                if verbose:
                    print "  Conversation about this PR onging"
                break

        #----------------------------------------------------------------------------
        # OK, this PR is done! Now let's print out the list of actions we tallied.
        #
        # In assisted mode, we will ask the user whether we want to take the
        # recommended actions.
        #
        # In autonomous mode (future), we will take the actions automatically.
        #----------------------------------------------------------------------------

        print " "
        print "RECOMMENDED ACTIONS for ", pull_request['html_url']
        if actions == []:
            print "  None required"
        else:
            for action in actions:
                print "  ", action

        print " "

        apply_actions = False
        if args.dry_run:
            print "In --dry-run mode so skipping actions."
            apply_actions = False
        elif not actions:
            print "No actions found, so nothing to do."
            apply_actions = False
        else:
            # If there are actions, ask if we should take them. Otherwise, skip.
            if args.pause_before_actions:
                action_query_result = raw_input("Take recommended actions (y/N)?")
                apply_actions = action_query_result in ('Y', 'y')

        if apply_actions:
            #------------------------------------------------------------------------
            # Now we start actually writing to the issue itself.
            #------------------------------------------------------------------------
            print "LABELS_URL: ", issue['labels_url']
            print "COMMENTS_URL: ", pull_request['comments_url']
            for action in actions:

                if "unlabel" in action:
                    oldlabel = action.split(': ')[-1]
                    # Don't remove it if it isn't there
                    if oldlabel in pr_labels:
                        pr_actionurl = issue['labels_url'].split("{")[0] + "/" + oldlabel
                        # print "URL for DELETE: ", pr_actionurl
                        try:
                            r = requests.delete(pr_actionurl, auth=(ghuser,ghpass))
                            # print r.text
                        except requests.exceptions.RequestException as e:
                            print e
                            sys.exit(1)

                if "newlabel" in action:
                    newlabel = action.split(': ')[-1]
                    if newlabel not in pr_labels:
                        pr_actionurl = issue['labels_url'].split("{")[0]
                        payload = '["' + newlabel +'"]'
                        # print "URL for POST: ", pr_actionurl
                        # print "  PAYLOAD: ", payload
                        try:
                            r = requests.post(pr_actionurl, data=payload, auth=(ghuser, ghpass))
                            # print r.text
                        except requests.exceptions.RequestException as e:
                            print e
                            sys.exit(1)

                if "boilerplate" in action:
                    # A hack to make the @ signs line up for multiple maintainers
                    mtext = pr_maintainers.replace(' ', ' @')
                    stext = pr_submitter
                    boilerout = action.split(': ')[-1]
                    newcomment = boilerplate[boilerout].format(m=mtext,s=stext)
                    payload = '{"body": "' + newcomment + '"}'
                    pr_actionurl = issue['comments_url']
                    # print "URL for POST: ", pr_actionurl
                    # print "  PAYLOAD: ", payload
                    try:
                        signal.alarm(5)
                        r = requests.post(pr_actionurl, data=payload, auth=(ghuser, ghpass))
                        signal.alarm(0)
                        # print r.text
                    except requests.exceptions.RequestException as e:
                        print e
                        sys.exit(1)



def get_pull_requests(session, repo_url, repo_url_params):
    # First, get number of pages using pagination in Link Headers. Thanks
    # requests library for making this relatively easy!
    signal.alarm(5)
    r = session.get(repo_url, params=repo_url_params)
    signal.alarm(0)
    lastpage = int(str(r.links['last']['url']).split('=')[-1])

    # Set range for 1..2 for testing only
    # for page in range(1,2):

    pull_requests = []
    skipped_pull_request_numbers = []

    for page in range(1, lastpage):
        pull_args = {'state':'open', 'page':page}
        signal.alarm(5)
        r = session.get(repo_url, params=pull_args)
        signal.alarm(0)

        open_prs = r.json()
        for open_pr in open_prs:

            # Do some nifty triage!
            if (int(open_pr['number']) <= int(repo_url.start_at)):
                pull_requests.append(open_pr['url'])
            else:
                # FIXME: do we need this?
                skipped_pull_request_numbers.append(open_pr['number'])

    return get_pull_requests

def main(args=None):
    args = args or None
    cli = Cli()

    if cli.options.debug:
        github.enable_console_debug_logging()
    pr_bot = PrBot(options=cli.options)
    gh_repo = 'ansible-modules-%s' % cli.options.ghrepo

    # FIXME: replace with github url soon
    repo_url = RepoUrl(gh_api_url=GITHUB_API_URL,
                    gh_org='ansible',
                    gh_repo=gh_repo,
                    state=cli.options.state,
                    start_at=cli.options.start_at)

    # If a pr number was given on the cli, triage just it.
    if cli.options.pr:
        pull_request = PullRequest(gh_org='ansible',
                                   gh_repo='ansible-modules-%s' % cli.options.ghrepo,
                                   pr=cli.options.pr)
        pr_bot.triage_pull_request(pull_request)
    else:
        pr_bot.triage_repo("%s/%s" % (cli.options.ghorg, gh_repo))

    # FIXME: track better error codes
    return triage(pull_requests)

if __name__ == "__main__":
    sys.exit(main(args=sys.argv[:]))
