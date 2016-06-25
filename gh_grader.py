import argparse
import shutil
import subprocess
import sys
import time
import os

from github import Github

def get_username(repo):
    contribs = repo.get_contributors()
    return contribs[0].login

def get_filename_without_ext(path):
    basename = os.path.basename(path)
    return os.path.splitext(basename)[0]

class RepoInfo:
    def __init__(self, args):
        self.args = args
        self.phases = ['update', 'build', 'javadoc', 'test']
        if args.skip:
            for skip in args.skip:
                self.phases.remove(skip)
        self.source_path = os.path.join(args.path, 'source')
        self.javadoc_path = os.path.join(args.path, 'javadoc')
        self.__get_repos()
        self.__get_ownership_info()

    def __get_repos(self):
        token = open('token', 'r')
        data = token.read()
        token.close()
        g = Github(data.strip())
        org = g.get_organization(self.args.org)
        all_gh_repos = org.get_repos()
        self.source_repos = [r for r in all_gh_repos if self.args.lab in r.name and 'javadoc' not in r.name]
        print 'Found {0} source repos'.format(len(self.source_repos))
        self.javadoc_repos = [r for r in all_gh_repos if self.args.lab in r.name and 'javadoc' in r.name]
        print 'Found {0} javadoc repos'.format(len(self.javadoc_repos))

    def __get_ownership_info(self):
        print 'Calculating repo ownership information...'
        self.owners = {}
        for repo in self.source_repos:
            owner = get_username(repo)
            print repo.name, '-->', owner
            self.owners[repo.name] = owner
        if 'javadoc' in self.phases:
            for repo in self.javadoc_repos:
                owner = get_username(repo)
                print repo.name, '-->', owner
                self.owners[repo.name] = owner

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

def clone_repos(repos, parent_dir):
    count = 0
    time.sleep(1)
    for repo in repos:
        count += 1
        clone_or_update(parent_dir, repo, count)

def create_if_not_exists(dir):
    if not os.path.exists(dir):
        print 'Creating directory:', dir
        os.makedirs(dir)
        
def update_phase(repo_info):
    if 'update' not in repo_info.phases:
        return
    create_if_not_exists(repo_info.source_path)
    print '\nCloning source repos...'
    clone_repos(repo_info.source_repos, repo_info.source_path)
    if 'javadoc' in repo_info.phases:
        create_if_not_exists(repo_info.javadoc_path)
        print '\nCloning javadoc repos...'
        clone_repos(repo_info.javadoc_repos, repo_info.javadoc_path)

def parse_ant_output(output):
    lines = output.split('\n')
    test_results = {}
    suite = None
    for line in lines:
        if '[junit] Testsuite:' in line:
            suite = line.strip()[19:]
        elif '[junit] Tests run:' in line and suite:
            translated = line.strip().translate(None, ',')
            segments = translated.split()
            total = int(segments[3])
            errors = int(segments[5]) + int(segments[7]) + int(segments[9])
            test_results[suite] = (total,errors)
            suite = None
    return test_results
        
def build_source_repo(repo, parent_dir):
    repo_path = os.path.join(parent_dir, repo.name)
    print '\nBuilding source repo {}'.format(repo_path)
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
    test_results = parse_ant_output(output[0])
    return p.returncode == 0, test_results
        
def build_phase(repo_info):
    if 'build' not in repo_info.phases:
        return {}
    build_status = {}
    for repo in repo_info.source_repos:
        status, test_results = build_source_repo(repo, repo_info.source_path)
        build_status[repo_info.owners[repo.name]] = (status, test_results)
    return build_status

def validate_javadoc_repo(repo):
    return repo.default_branch == 'gh-pages' and not repo.private

def javadoc_phase(repo_info):
    if 'javadoc' not in repo_info.phases:
        return {}
    javadoc_status = {}
    for repo in repo_info.javadoc_repos:
        javadoc_status[repo_info.owners[repo.name]] = validate_javadoc_repo(repo)
    return javadoc_status

def test_source_repo(test_class, repo, parent_dir):
    repo_path = os.path.join(parent_dir, repo.name)
    basename = os.path.basename(test_class)
    suite = get_filename_without_ext(test_class)
    src_path = os.path.join(repo_path, 'src')
    if not os.path.exists(src_path):
        print 'Failed to locate the src directory:', src_path
        return False, None
    shutil.copy(test_class, src_path)
    print '\nTesting source repo {}'.format(repo_path)
    print '======================================================='
    p = subprocess.Popen(['ant', 'test'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = p.communicate()
    print output[0]
    if output[1]:
        print output[1]
    print 'Build exited with status', p.returncode
    os.remove(os.path.join(src_path, basename))
    test_results = parse_ant_output(output[0])
    return p.returncode == 0, test_results

def test_phase(repo_info):
    if 'test' not in repo_info.phases:
        return {}
    test_status = {}
    if repo_info.args.test_class:
        for repo in repo_info.source_repos:
            status, test_results = test_source_repo(repo_info.args.test_class, repo, repo_info.source_path)
            test_status[repo_info.owners[repo.name]] = (status, test_results)
    return test_status

def test_result_summary(results, suite=None):
    total = 0
    errors = 0
    if results[1]:
        for k,v in results[1].items():
            if suite is None or k == suite:
                total += v[0]
                errors += v[1]
    return '{0}/{1}'.format(total-errors, total)

def print_output_header(repo_info):
    print '\n\nResults Summary'
    print '===================='
    header = '[summary] Repo Owner'
    if 'build' in repo_info.phases:
        header += ' StudentBuild StudentTests'
    if 'javadoc' in repo_info.phases:
        header += ' Javadoc'
    if 'test' in repo_info.phases and repo_info.args.test_class:
        header += ' InstructorBuild InstructorTests'
    print header

if __name__  == '__main__':
    parser = argparse.ArgumentParser(description='Grades a lab by downloading student submissions from Github.com.')
    parser.add_argument('--org', '-o', dest='org', default='UCSB-CS56-M16')
    parser.add_argument('--lab', '-l', dest='lab', default=None)
    parser.add_argument('--path', '-p', dest='path', default='repos')
    parser.add_argument('--test-class', '-t', dest='test_class', default=None)
    parser.add_argument('--skip', '-s', dest='skip', nargs='*')
    args = parser.parse_args()

    if not args.lab:
        print 'Lab not specified'
        sys.exit(1)

    repo_info = RepoInfo(args)
    update_phase(repo_info)
    build_status = build_phase(repo_info)        
    javadoc_status = javadoc_phase(repo_info)
    test_status = test_phase(repo_info)
    
    print_output_header(repo_info)
    for repo in repo_info.source_repos:
        owner = repo_info.owners[repo.name]
        summary = ['[summary]', repo.name, owner]
        if 'build' in repo_info.phases:
            result = build_status[owner]
            summary.append(result[0])
            summary.append(test_result_summary(result))
        if 'javadoc' in repo_info.phases:
            summary.append(javadoc_status.get(owner, '-'))
        if 'test' in repo_info.phases:
            if test_status:
                result = test_status[owner]
                summary.append(result[0])
                summary.append(test_result_summary(result))
        for item in summary:
            print item,
        print
