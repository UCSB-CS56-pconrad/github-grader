import argparse
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
            
def validate_source_repo(repo, parent_dir):
    repo_path = os.path.join(parent_dir, repo.name)
    print '\nValidating source repo {}'.format(repo_path)
    print '======================================================='
    p = subprocess.Popen(['ant', 'test'], cwd=repo_path)
    status = p.wait()
    print 'Build exited with status', status
    return status

def validate_javadoc_repo(repo):
    return repo.default_branch == 'gh-pages' and not repo.private

def get_username(repo):
    contribs = repo.get_contributors()
    return contribs[0].login

def create_if_not_exists(dir):
    if not os.path.exists(dir):
        print 'Creating directory:', dir
        os.makedirs(dir)
    
if __name__  == '__main__':
    parser = argparse.ArgumentParser(description='Grades a lab by downloading student submissions from Github.com.')
    parser.add_argument('--org', '-o', dest='org', default='UCSB-CS56-M16')
    parser.add_argument('--lab', '-l', dest='lab', default=None)
    parser.add_argument('--path', '-p', dest='path', default='repos')
    args = parser.parse_args()

    if not args.lab:
        print 'Lab not specified'
        sys.exit(1)

    source_path = os.path.join(args.path, 'source')
    create_if_not_exists(source_path)
    javadoc_path = os.path.join(args.path, 'javadoc')
    create_if_not_exists(javadoc_path)
    
    token = open('token', 'r')
    data = token.read()
    token.close()
    g = Github(data.strip())
    org = g.get_organization(args.org)
    all_gh_repos = org.get_repos()
    
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
        
    clone_source_repos(source_repos, source_path)
    clone_javadoc_repos(javadoc_repos, javadoc_path)

    build_status = {}
    for repo in source_repos:
        status = validate_source_repo(repo, source_path)
        build_status[repo_owners[repo.name]] = bool(status)
        
    javadoc_status = {}
    for repo in javadoc_repos:
        javadoc_status[repo_owners[repo.name]] = validate_javadoc_repo(repo)

    print '\n\nResults Summary'
    print '===================='
    print '[summary] Repo Owner BuildStatus JavadocStatus'
    for repo in source_repos:
        owner = repo_owners[repo.name]
        print '[summary]', repo.name, owner, build_status.get(owner, '-'), javadoc_status.get(owner, '-')
