import os
import utils
import git
import re
import logging
import requests
import unidiff
import urllib2
import codecs
from utils import parse_patch as _pw_parse
from urlparse import urlparse

logger = logging.getLogger('patchtest')

class BaseMboxItem(object):
    """ mbox item containing all data extracted from an mbox, and methods
        to extract this data. This base class and should be inherited
        from, not directly instantiated.
    """
    MERGE_STATUS_INVALID = 'INVALID'
    MERGE_STATUS_NOT_MERGED = 'NOTMERGED'
    MERGE_STATUS_MERGED_SUCCESSFULL = 'PASS'
    MERGE_STATUS_MERGED_FAIL = 'FAIL'
    MERGE_STATUS = (MERGE_STATUS_INVALID,
                    MERGE_STATUS_NOT_MERGED,
                    MERGE_STATUS_MERGED_SUCCESSFULL,
                    MERGE_STATUS_MERGED_FAIL)

    def __init__(self, resource, args=None, forcereload=False):
        self._resource = resource
        self._args = args
        self._forcereload = forcereload

        self._contents = ''
        self._status = BaseMboxItem.MERGE_STATUS_NOT_MERGED

        self._keyvals = {}
        self._patchdiff = ''
        self._commentbuf = ''
        self._changes = None

    @property
    def contents(self):
        raise(NotImplementedError, 'Please do not instantiate MboxItem; patch reading method depends on Mbox type')

    @property
    def is_empty(self):
        _cnt = self.contents
        return not(_cnt.strip())

    def _load_diff_comment(self):
        """
        Use the patchwork parser to generate a patchdiff (the actual patch)
        and a comment buffer (the patch metadata) for this object
        """
        _contents = self.contents
        if self._forcereload or (not self._patchdiff or not self._commentbuf):
            self._patchdiff, self._commentbuf = _pw_parse(_contents)

    @property
    def diff(self):
        if self._forcereload or (not self._patchdiff):
            self._load_diff_comment()
        return self._patchdiff

    @property
    def comments(self):
        if self._forcereload or (not self._commentbuf):
            self._load_diff_comment()
        return self._commentbuf

    @property
    def keyvals(self):
        if self._forcereload or (not self._keyvals):
            self._parse_keyvals()
        return self._keyvals

    def _parse_keyvals(self, pattern='^([\w-]+):\s+(.*)$'):
        """
        Parse key-value pairs out of this item's patch metadata
        """
        _cmt = self.comments
        cmt_seps = ('---', 'diff')
        cmt_head = _cmt

        for _sep in cmt_seps:
            sep_index = _cmt.find(_sep)
            if sep_index >= 0:
                cmt_head = _cmt[:sep_index]
                break

        for _line in [ _.strip() for _ in cmt_head.splitlines()[1:] ]:
            _m = re.match(pattern, _line)
            if _m and _m.groups():
                _k, _v = _m.groups()
                utils.dict_append_new(self._keyvals, _k, _v)
            else:
                if _line:
                    utils.dict_append_new(self._keyvals, 'Description', _line)

    @property
    def changes(self):
        raise(NotImplementedError, 'Please do not instantiate MboxItem; patch change scanning depends on Mbox type')

    def getresource(self):
        resource = self._resource
        if self._args:
            resource %= self._args
        return resource

    def setresource(self, resouce):
        self._resource = resource

    resource = property(getresource, setresource)

    def getargs(self):
        return self._args

    def setargs(self, args):
        self._args = args

    args = property(getargs, setargs)

    def getstatus(self):
        return self._status

    def setstatus(self, status):
        if not status in BaseMboxItem.MERGE_STATUS:
            logger.warn('Status (%s) not valid' % status)
        else:
            self._status = status

    status = property(getstatus, setstatus)

    def getbranch(self):
        return utils.get_branch(self.contents)

class MboxURLItem(BaseMboxItem):
    """ Mbox item based on a URL"""

    @property
    def contents(self):
        if self._forcereload or (not self._contents):
            logger.debug('Reading %s contents' % self.resource)
            try:
                _r = requests.get(self.resource)
                self._contents = _r.text
            except:  #XXX: narrow down to more specific exceptions
                logger.warn("Request to %s failed" % self.resource)
        return self._contents

    @property
    def changes(self):
        if self._forcereload or (not self._changes):
            try:
                _url = urllib2.urlopen(self.resource)
            except:  #XXX: narrow down to more specific exceptions
                logger.warn("Request to %s failed" % self.resource)
            try:
                self._changes = unidiff.PatchSet(_url)
            except:  #XXX: narrow down to more specific exceptions
                logger.warn("Parsing %s failed" % self.resource)
        return self._changes

    @property
    def pretty_resource(self):
        if not self.resource:
            return ''
        else:
            _url = urlparse(str(self.resource))
            _inst = "%s://%s" % (_url.scheme, _url.netloc)
            _series = self.args[0]
            _revision = self.args[1]
            return "%s/series/%s/#rev%s" % (_inst, _series, _revision)

class MboxFileItem(BaseMboxItem):
    """ Mbox item based on a file"""

    @property
    def contents(self):
        if self._forcereload or (not self._contents):
            logger.debug('Reading %s contents' % self.resource)
            try:
                with open(self.resource) as _f:
                    self._contents = _f.read()
            except IOError:
                logger.warn("Reading the mbox %s failed" % self.resource)
        return self._contents

    @property
    def changes(self):
        if self._forcereload or (not self._changes):
            try:
                with codecs.open(self.resource) as _f:
                    self._changes = unidiff.PatchSet(_f)
            except IOError:
                logger.warn("Reading the mbox %s failed" % self.resource)
        return self._changes

    @property
    def pretty_resource(self):
        return "%s" % os.path.basename(self.resource) if self.resource else 'a patch file'

class Repo(object):

    # prefixes used for temporal branches/stashes
    prefix = 'patchtest'

    def __init__(self, repodir, commit=None, branch=None, mbox=None, series=None, revision=None):
        self._repodir = repodir
        self._mbox = mbox

        try:
            self.repo = git.Repo(self._repodir)
        except git.exc.InvalidGitRepositoryError:
            logger.error('Not a git repository')
            raise Exception

        config = self.repo.config_reader()

        try:
            patchwork_section = 'patchwork "%s"' % 'default'
            self._url = config.get(patchwork_section, 'url')
            self._project = config.get(patchwork_section, 'project')
        except:
            logger.error('patchwork url/project configuration is not available')
            raise Exception

        self._series_revision = self._get_series_revisions(series, revision)

        # create the items
        self._mboxitems = self._create_items()

        # get current branch name, so we can checkout at the end
        self._current_branch = self._get_current_branch()

        # branch to be used for testing, priority: branch provided by
        # user, branch defined in item/items or current branch
        self._branch = branch or self._get_items_branch() or self._current_branch
        self._commit = self._get_commitid(commit or self._branch)
        self._branchname = "%s_%s" % (Repo.prefix, os.getpid())
        self._mailinglist = self._get_mailinglist()

        # for debugging purposes, print all repo parameters
        logger.debug("Parameters")
        logger.debug("\tRepository: %s" % self._repodir)
        logger.debug("\tCommit: %s" % self._commit)
        logger.debug("\tBranch: %s" % self._branch)
        logger.debug("\tMBOX: %s" % self._mbox)
        logger.debug("\tSeries/Revision: %s" % self._series_revision)

    @property
    def items(self):
        """ Items to be tested. By default it is initialized as an empty list"""
        return self._mboxitems

    @property
    def url(self):
        return self._url

    @property
    def project(self):
        return self._project

    @property
    def mailinglist(self):
        return self._mailinglist

    @property
    def branch(self):
        return self._branch

    @property
    def commit(self):
        return self._commit

    def _get_mailinglist(self, defaultml=''):
        ml = defaultml
        url = "%s/api/1.0/projects/%s" % (self._url, self._project)
        try:
            r = requests.get(url)
            ml = r.json()['listemail']
        except Exception as e:
            logger.warn("Mailing list could not be fetched")
        return ml

    def _create_items(self):
        """ Load MboxItems to be tested """
        _mboxitems = []
        if self._mbox:
            for _eachitem in self._mbox:
                _mboxitems.append(MboxFileItem(_eachitem))
        elif self._series_revision:
            fullurl = "%s" % self._url
            fullurl +="/api/1.0/series/%s/revisions/%s/mbox/"
            for s,r in self._series_revision:
                _mboxitems.append(MboxURLItem(fullurl, (s,r)))
        return _mboxitems

    def _exec(self, cmds):
        _cmds = []
        if isinstance(cmds, dict):
            _cmds.append(cmds)
        elif isinstance(cmds, list):
            _cmds = cmds
        else:
            raise utils.CmdException({'cmd':str(cmds)})

        results = []
        try:
            results = utils.exec_cmds(_cmds, self._repodir)
        except utils.CmdException as ce:
            cmd = ' '.join(ce.cmd)
            logger.error("CMD: %s" % cmd)
            logger.debug("CMD: %s RCODE: %s STDOUT: %s STDERR: %s" %
                         (cmd, ce.returncode, ce.stdout, ce.stderr))
            raise ce

        if logger.getEffectiveLevel() == logging.DEBUG:
            for result in results:
                logger.debug("CMD: %s RCODE: %s STDOUT: %s STDERR: %s" %
                             (' '.join(result['cmd']), result['returncode'], result['stdout'], result['stderr']))

        return results

    def _get_current_branch(self, commit='HEAD'):
        cmd = {'cmd':['git', 'rev-parse', '--abbrev-ref', commit]}
        return self._exec(cmd)[0]['stdout']

    def _get_items_branch(self):
        branch = None
        branches = [b.getbranch() for b in self._mboxitems]
        if branches and all(branches):
            head, tail = branches[0], branches[1:]
            if tail:
                # in case of multiple items, all items should target
                # the same branch
                if all(map(lambda b: head == b, tail)):
                    branch = head
            else:
                branch = head

        if branch:
            logger.debug('branch name detected on mbox: %s' % branch)
            # now check if the branch that the item is refering to actually exists on the repo
            res = self._exec({'cmd':['git', 'branch', '--remote', '--list', '*/%s' % branch]})
            remote_branch = res[0]['stdout']
            if remote_branch:
                logger.debug('Remote branch to be used: %s' % remote_branch)
                branch = remote_branch
            else:
                branch = None

        return branch

    def _get_commitid(self, commit='HEAD'):
        cmd = {'cmd':['git', 'rev-parse', '--short', commit]}
        return self._exec(cmd)[0]['stdout']

    def _fetch_latest_revision(self, series, defaultrev=1):
        revision = None
        url = "%s/api/1.0/series/%s" % (self._url, series)
        try:
            r = requests.get(url)
            rjson = r.json()
            revision = rjson['version']
        except Exception:
            revision = defaultrev
            logger.warn("latest series' revision could not be obtained from patchwork, using default revision %s" %
                        defaultrev)
        return revision

    def _get_series_revisions(self, listseries, listrevisions, defaultrev=1):
        res = []

        if not listseries:
            return res

        if not listrevisions:
            for series in listseries:
                revision = self._fetch_latest_revision(series)
                res.append((series, revision))
        else:
            if len(listseries) != len(listrevisions):
                logger.warn("The number of series and revisions are different")

            for series, revision in zip(listseries, listrevisions):
                if not revision:
                    latest_revision = self._fetch_latest_revision(series)
                    res.append((series, latest_revision))
                else:
                    res.append((series, revision))
        return res

    def _merge_item(self, item):
        contents = item.contents

        if not contents:
            logger.error('Contents are empty')
            raise Exception

        self._exec([
            {'cmd':['git', 'apply', '--check', '--verbose'], 'input':contents},
            {'cmd':['git', 'am'], 'input':contents, 'updateenv':{'PTRESOURCE':item.resource}}]
        )

    def merge(self):
        # create the branch before merging
        self._exec([
            {'cmd':['git', 'checkout', self._branch]},
            {'cmd':['git', 'checkout', '-b', self._branchname, self._commit]},
        ])

        # merge the items
        for item in self._mboxitems:
            try:
                self._merge_item(item)
                item.status = BaseMboxItem.MERGE_STATUS_MERGED_SUCCESSFULL
            except utils.CmdException as ce:
                item.status = BaseMboxItem.MERGE_STATUS_MERGED_FAIL
            except:
                item.status = BaseMboxItem.MERGE_STATUS_INVALID

    def any_merge(self):
        for item in self._mboxitems:
            if item.status == BaseMboxItem.MERGE_STATUS_MERGED_SUCCESSFULL:
                return True
        return False

    def clean(self, keepbranch=False):
        """ Leaves the repo as it was before testing """
        cmds = [
            {'cmd':['git', 'checkout', '%s' % self._current_branch]},
        ]

        if not keepbranch:
            cmds.append({'cmd':['git', 'branch', '-D', self._branchname], 'ignore_error':True})

        self._exec(cmds)

    def post(self, testname, state, summary):
        # TODO: for the moment, all elements on self._series_revision share the same
        # result. System should post independent results for each _series_revision
        for series, revision in self._series_revision:
            cmd = {'cmd':['git', 'pw', 'post-result',
                          series,
                          testname,
                          state,
                          '--summary', summary,
                          '--revision', revision]}
            try:
                self._exec(cmd)
            except utils.CmdException:
                logger.warn('POST requests cannot be done to %s' % self._url)

