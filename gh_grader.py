import argparse
import shutil
import subprocess
import sys
import time
import os
import yaml

from github import Github

class Context:
    def __init__(self, config):
        self.config = config

    def phase_enabled(self, name):
        phase = self.config.get(name, {})
        return phase.get('enabled', True)
        
    def get_config(self, name, default):
        segments = name.split('.')
        map = self.config
        for segment in segments:
            map = map.get(segment, None)
            if map is None:
                return default
        return map

class Repo:
    def __init__(self, repo, owner):
        self.name = repo.name
        self.clone_url = repo.clone_url
        self.default_branch = repo.default_branch
        self.private = repo.private
        self.owner = owner
    
class InitPhase:
    name = 'init'
    
    def __init__(self, context):
        self.context = context
        self.target = context.get_config('init.target', 'target')
        self.org_name = context.get_config('init.org', None)
        if self.org_name is None:
            raise Exception('Github organization not specified')
        self.source_prefix = context.get_config('init.source_prefix', None)
        if self.source_prefix is None:
            raise Exception('Source prefix not specified')
        self.javadoc_prefix = context.get_config('init_javadoc_prefix', self.source_prefix + 'javadoc_')

    def __get_github_client(self):
        token = open('token', 'r')
        data = token.read()
        token.close()
        return Github(data.strip())
        
    def run(self):
        self.context.source_path = os.path.join(self.target, 'source')
        self.context.javadoc_path = os.path.join(self.target, 'javadoc')
        g = self.__get_github_client()
        org = g.get_organization(self.org_name)
        all_gh_repos = org.get_repos()
        members = org.get_members()
        source_repos = []
        javadoc_repos = []
        for member in members:
            source_repo = self.source_prefix + member.login
            javadoc_repo = self.javadoc_prefix + member.login
            for r in all_gh_repos:
                if r.name == source_repo:
                    source_repos.append(Repo(r, member.login))
                elif r.name == javadoc_repo:
                    javadoc_repos.append(Repo(r, member.login))
        print 'Found {0} source repos'.format(len(source_repos))
        print 'Found {0} javadoc repos'.format(len(javadoc_repos))
        self.context.source_repos = source_repos
        self.context.javadoc_repos = javadoc_repos

class UpdatePhase:
    name = 'update'
    
    def __init__(self, context):
        self.context = context
        self.enabled = context.phase_enabled('update')

    @staticmethod
    def create_if_not_exists(dir):
        if not os.path.exists(dir):
            print 'Creating directory:', dir
            os.makedirs(dir)

    @staticmethod
    def clone_or_update(parent_dir, repo, count):
        repo_path = os.path.join(parent_dir, repo.name)
        if os.path.exists(os.path.join(repo_path)):
            print '\n[{0}] Pulling from repo {1}'.format(count, repo.clone_url)
            print '==========================================================='
            p = subprocess.Popen(['git', 'pull'], cwd=repo_path)
            p.wait()
        else:
            print '\n[{0}] Cloning from repo {1}'.format(count, repo.clone_url)
            print '==========================================================='
            p = subprocess.Popen(['git', 'clone', repo.clone_url], cwd=parent_dir)
            p.wait()

    @staticmethod
    def clone_repos(repos, parent_dir):
        count = 0
        time.sleep(1)
        for repo in repos:
            count += 1
            UpdatePhase.clone_or_update(parent_dir, repo, count)

    def run(self):
        if not self.enabled:
            return
        UpdatePhase.create_if_not_exists(self.context.source_path)
        print '\nCloning source repos...'
        UpdatePhase.clone_repos(self.context.source_repos, self.context.source_path)
        if self.context.phase_enabled('javadoc'):
            UpdatePhase.create_if_not_exists(self.context.javadoc_path)
            print '\nCloning javadoc repos...'
            UpdatePhase.clone_repos(self.context.javadoc_repos, self.context.javadoc_path)
        
class BuildPhase:
    name = 'build'
    
    def __init__(self, context):
        self.context = context
        self.enabled = context.phase_enabled('build')
        self.build_status = {}

    @staticmethod
    def build_source_repo(repo, parent_dir):
        repo_path = os.path.join(parent_dir, repo.name)
        print '\nBuilding source repo {}'.format(repo_path)
        print '======================================================='
        if not os.path.exists(repo_path):
            print 'No local copy available'
            return False
        p = subprocess.Popen(['ant', 'compile'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = p.communicate()
        print output[0]
        if output[1]:
            print output[1]
        print 'Build exited with status', p.returncode
        return p.returncode == 0
        
    def run(self):
        if not self.enabled:
            return
        for repo in self.context.source_repos:
            status = BuildPhase.build_source_repo(repo, self.context.source_path)
            self.build_status[repo.owner] = status

class TestResult:
    def __init__(self, suite, total, errors):
        self.suite = suite
        self.total = total
        self.errors = errors

    def __str__(self):
        return '{0}/{1}'.format(self.total - self.errors, self.total)
            
class TestPhase:
    name = 'test'
    
    def __init__(self, context):
        self.context = context
        self.enabled = context.phase_enabled('test')
        self.test_status = {}
        self.test_results = {}

    def get_summary(self, owner):
        total = 0
        errors = 0
        results = self.test_results.get(owner)
        if results is None:
            results = []
        for tr in results:
            total += tr.total
            errors = tr.errors
        return TestResult('summary', total, errors)

    @staticmethod
    def parse_ant_output(output):
        lines = output.split('\n')
        test_results = []
        suite = None
        for line in lines:
            if '[junit] Testsuite:' in line:
                suite = line.strip()[19:]
            elif '[junit] Tests run:' in line and suite:
                translated = line.strip().translate(None, ',')
                segments = translated.split()
                total = int(segments[3])
                errors = int(segments[5]) + int(segments[7]) + int(segments[9])
                test_results.append(TestResult(suite, total, errors))
                suite = None
        return test_results

    @staticmethod
    def test_source_repo(repo, parent_dir):
        repo_path = os.path.join(parent_dir, repo.name)
        print '\nTesting source repo {}'.format(repo_path)
        print '======================================================='
        if not os.path.exists(repo_path):
            print 'No local copy available'
            return False, None
        p = subprocess.Popen(['ant', 'test'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = p.communicate()
        print output[0]
        if output[1]:
            print output[1]
        print 'Build exited with status', p.returncode
        if p.returncode == 0:
            return True, TestPhase.parse_ant_output(output[0])
        return False, None
        
    def run(self):
        if not self.enabled:
            return
        for repo in self.context.source_repos:
            status, results = TestPhase.test_source_repo(repo, self.context.source_path)
            self.test_status[repo.owner] = status
            self.test_results[repo.owner] = results

class JavadocPhase:
    name = 'javadoc'
    
    def __init__(self, context):
        self.context = context
        self.enabled = context.phase_enabled('javadoc')
        self.javadoc_status = {}

    @staticmethod
    def validate_javadoc_repo(repo):
        return repo.default_branch == 'gh-pages' and not repo.private

    def run(self):
        if not self.enabled:
            return
        for repo in self.context.javadoc_repos:
            self.javadoc_status[repo.owner] = JavadocPhase.validate_javadoc_repo(repo)

class ValidatePhase:
    name = 'validate'
    
    def __init__(self, context):
        self.context = context
        self.enabled = context.phase_enabled('validate')
        self.test_class = context.get_config('validate.test_class', None)
        if self.test_class:
            basename = os.path.basename(self.test_class)
            self.test_suite_name = os.path.splitext(basename)[0]
        else:
            self.test_suite_name = None
        self.test_status = {}
        self.test_results = {}

    def get_summary(self, owner):
        total = 0
        errors = 0
        results = self.test_results.get(owner)
        if results is None:
            results = []
        for tr in results:
            total += tr.total
            errors = tr.errors
        return TestResult('summary', total, errors)

    @staticmethod
    def test_source_repo(test_class, repo, parent_dir):
        repo_path = os.path.join(parent_dir, repo.name)
        src_path = os.path.join(repo_path, 'src')
        if not os.path.exists(src_path):
            print 'Failed to locate the src directory:', src_path
            return False, None
        shutil.copy(test_class, src_path)
        status, results = TestPhase.test_source_repo(repo, parent_dir)
        os.remove(os.path.join(src_path, os.path.basename(test_class)))
        return status, results
            
    def run(self):
        if not self.enabled or self.test_class is None:
            return
        for repo in self.context.source_repos:
            status, results = ValidatePhase.test_source_repo(self.test_class, repo, self.context.source_path)
            self.test_status[repo.owner] = status
            self.test_results[repo.owner] = results
            
def print_output_header(context):
    print '\n\nResults Summary'
    print '===================='
    header = '[summary] Repo Owner'
    if context.phase_enabled('build'):
        header += ' Build'
    if context.phase_enabled('test'):
        header += ' Test'
    if context.phase_enabled('javadoc'):
        header += ' Javadoc'
    if context.phase_enabled('validate'):
        header += ' Validate'
    print header

def get_phase(phases, name):
    for phase in phases:
        if phase.name == name:
            return phase
    return None
    
if __name__  == '__main__':
    parser = argparse.ArgumentParser(description='Grades a lab by downloading student submissions from Github.com.')
    parser.add_argument('--file', '-f', dest='file', default=None)
    args = parser.parse_args()

    if not args.file:
        print 'Configuration file not specified'
        sys.exit(1)

    with open(args.file, 'r') as f:
        doc = yaml.load(f)
    context = Context(doc)
    phases = [
        InitPhase(context),
        UpdatePhase(context),
        BuildPhase(context),
        TestPhase(context),
        JavadocPhase(context),
        ValidatePhase(context)
    ]

    for phase in phases:
        phase.run()
    print_output_header(context)
    for repo in context.source_repos:
        print '[summary] {0} {1}'.format(repo.name, repo.owner),
        if context.phase_enabled('build'):
            print get_phase(phases, 'build').build_status.get(repo.owner, '-'),
        if context.phase_enabled('test'):
            print get_phase(phases, 'test').get_summary(repo.owner),
        if context.phase_enabled('javadoc'):
            print get_phase(phases, 'javadoc').javadoc_status.get(repo.owner, '-'),
        if context.phase_enabled('validate'):
            print get_phase(phases, 'validate').get_summary(repo.owner),
        print
            
