import argparse
import shutil
import subprocess
import sys
import time
import os

from github import Github

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

def clone_source_repos(repos, parent_dir):
    count = 0
    print '\nCloning source code repos...'
    time.sleep(1)
    for repo in repos:
        if 'javadoc' not in repo.name:
            count += 1
            clone_or_update(parent_dir, repo, count)

def clone_javadoc_repos(repos, parent_dir):
    count = 0
    print '\nCloning javadoc repos...'
    time.sleep(1)
    for repo in repos:
        if 'javadoc' in repo.name:
            count += 1
            clone_or_update(parent_dir, repo, count)

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
            
def validate_source_repo(repo, parent_dir):
    repo_path = os.path.join(parent_dir, repo.name)
    print '\nValidating source repo {}'.format(repo_path)
    print '======================================================='
    p = subprocess.Popen(['ant', 'test'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = p.communicate()
    print 'Build exited with status', p.returncode
    print output[0]
    if p.returncode == 0:
        return True, parse_ant_output(output[0])
    return False, None

def instructor_validate_source_repo(test_class, repo, parent_dir):
    repo_path = os.path.join(parent_dir, repo.name)
    basename = os.path.basename(test_class)
    suite = os.path.splitext(basename)[0]
    src_path = os.path.join(repo_path, 'src')
    if not os.path.exists(src_path):
        print 'Failed to locate the src directory:', src_path
        return False, None
    shutil.copy(test_class, src_path)
    print '\nInstructor validating source repo {}'.format(repo_path)
    print '======================================================='
    p = subprocess.Popen(['ant', 'test'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = p.communicate()
    print 'Build exited with status', p.returncode
    print output[0]
    os.remove(os.path.join(src_path, basename))
    if p.returncode == 0:
        return True, parse_ant_output(output[0])
    return False, None

def validate_javadoc_repo(repo):
    return repo.default_branch == 'gh-pages' and not repo.private

def get_username(repo):
    contribs = repo.get_contributors()
    return contribs[0].login

def create_if_not_exists(dir):
    if not os.path.exists(dir):
        print 'Creating directory:', dir
        os.makedirs(dir)

def test_result_summary(results, suite=None):
    suite_name = None
    if suite:
        basename = os.path.basename(suite)
        suite_name = os.path.splitext(basename)[0]
    
    total = 0
    errors = 0
    for k,v in results.items():
        if suite_name is None or k == suite_name:
            total += v[0]
            errors += v[1]
    return '{0}/{1}'.format(total-errors, total)

def get_all_repos():
    token = open('token', 'r')
    data = token.read()
    token.close()
    g = Github(data.strip())
    org = g.get_organization(args.org)
    return org.get_repos()
    
if __name__  == '__main__':
    parser = argparse.ArgumentParser(description='Grades a lab by downloading student submissions from Github.com.')
    parser.add_argument('--org', '-o', dest='org', default='UCSB-CS56-M16')
    parser.add_argument('--lab', '-l', dest='lab', default=None)
    parser.add_argument('--path', '-p', dest='path', default='repos')
    parser.add_argument('--test-class', '-t', dest='test_class', default=None)
    parser.add_argument('--skip-update', '-s', dest='skip_update', action='store_true', default=False)
    args = parser.parse_args()

    if not args.lab:
        print 'Lab not specified'
        sys.exit(1)

    source_path = os.path.join(args.path, 'source')
    create_if_not_exists(source_path)
    javadoc_path = os.path.join(args.path, 'javadoc')
    create_if_not_exists(javadoc_path)
    
    all_gh_repos = get_all_repos()
    source_repos = [r for r in all_gh_repos if args.lab in r.name and 'javadoc' not in r.name]
    print 'Found {0} source repos'.format(len(source_repos))
    javadoc_repos = [r for r in all_gh_repos if args.lab in r.name and 'javadoc' in r.name]
    print 'Found {0} javadoc repos'.format(len(javadoc_repos))

    print 'Calculating repo ownership information...'
    repo_owners = {}
    for repo in source_repos:
        repo_owners[repo.name] = get_username(repo)
    for repo in javadoc_repos:
        repo_owners[repo.name] = get_username(repo)

    if not args.skip_update:
        clone_source_repos(source_repos, source_path)
        clone_javadoc_repos(javadoc_repos, javadoc_path)

    build_status = {}
    for repo in source_repos:
        status,test_results = validate_source_repo(repo, source_path)
        build_status[repo_owners[repo.name]] = (status,test_results)
        
    javadoc_status = {}
    for repo in javadoc_repos:
        javadoc_status[repo_owners[repo.name]] = validate_javadoc_repo(repo)

    instructor_test_status = {}
    if args.test_class:
        for repo in source_repos:
            status,test_results = instructor_validate_source_repo(args.test_class, repo, source_path)
            instructor_test_status[repo_owners[repo.name]] = (status,test_results)

    print '\n\nResults Summary'
    print '===================='
    print '[summary] Repo Owner BuildStatus StudentTests InstructorTests JavadocStatus'
    for repo in source_repos:
        owner = repo_owners[repo.name]
        result = build_status[owner]
        test_summary = '-'
        if result[0]:
            test_summary = test_result_summary(result[1])

        i_test_summary = '-'
        if args.test_class:
            i_result = instructor_test_status[owner]
            if i_result[0]:
                i_test_summary = test_result_summary(i_result[1], args.test_class)
        print '[summary]', repo.name, owner, result[0], test_summary, i_test_summary, javadoc_status.get(owner, '-')
