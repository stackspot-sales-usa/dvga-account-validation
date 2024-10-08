from modules import *
import requests
import json
import os
import sys
import concurrent.futures
from requests.auth import HTTPBasicAuth
import time
import yaml

def get_env_variable(var_name):
    value = os.getenv(var_name)
    if not value:
        print(f"Missing required environment variable: {var_name}")
        sys.exit(1)
    return value

def get_stk_bearer_token(client_id, client_secret, realm):
    url = f"https://idm.stackspot.com/{realm}/oidc/oauth/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "client_id": client_id,
        "grant_type": "client_credentials",
        "client_secret": client_secret
    }
    
    response = requests.post(url, headers=headers, data=data)
    response_data = response.json()
    #print(response_data)
    stk_access_token = response_data.get("access_token")
    
    if stk_access_token:
        return stk_access_token
    else:
        raise Exception("Failed to retrieve access stk_access_token")

def create_rqc_execution(qc_slug, stk_access_token, input_data, file_name):
    url = f"https://genai-code-buddy-api.stackspot.com/v1/quick-commands/create-execution/{qc_slug}"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {stk_access_token}'
    }
    data = {
        'input_data': input_data
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        decoded_content = response.content.decode('utf-8')  # Decode bytes to string
        extracted_value = decoded_content.strip('"')  # Strip the surrounding quotes
        response_data = extracted_value
        print(f'{os.path.basename(file_name)} ExecutionID:', response_data)
        return response_data
    else:
        print(f'{file_name} stackspot create rqc api response:{response.status_code}')
        return None

def get_execution_status(execution_id, stk_access_token,file_name, qc_timeout_limit, input_data, qc_slug):
    url = f"https://genai-code-buddy-api.stackspot.com/v1/quick-commands/callback/{execution_id}"
    headers = {'Authorization': f'Bearer {stk_access_token}'}
    i = 0
    retries=3 #the number of retries after a failed API call
    retry_count = 0
    
    while True:
        try:
            # Make the API call
            response = requests.get(url, headers=headers)
            
            # Check if the response is successful
            if response.status_code == 200:
                response_data = response.json()
                status = response_data['progress']['status']
                
                if status == 'COMPLETED':
                    print(f"{os.path.basename(file_name)}: Execution complete!")
                    return response_data
                if status == 'FAILURE':
                    print(f"ERROR: The execution fot the file {os.path.basename(file_name)} FAILED, restarting execution now!")
                    return execute_qc_and_get_response(stk_access_token, qc_slug, input_data, file_name, qc_timeout_limit)
                else:
                    print(f"{os.path.basename(file_name)}: Status: {status} ({i} seconds elapsed)")
                    print(f"{os.path.basename(file_name)}: Execution in progress, waiting...")
                    i += 5
                    time.sleep(5)  # Wait for 5 seconds before polling again
            else:
                # If the response is not successful, raise an exception to trigger retry
                raise Exception(f"{os.path.basename(file_name)}: API call failed with status code {response.status_code}")
        
        except Exception as e:
            print(f"{os.path.basename(file_name)}: Error: {e}")
            retry_count += 1
            
            if retry_count >= retries:
                print(f"{os.path.basename(file_name)}: Error: Maximum retries reached. Giving up.")
                return execute_qc_and_get_response(stk_access_token, qc_slug, input_data, file_name, qc_timeout_limit)
            else:
                print(f"{os.path.basename(file_name)}: Retrying... Attempt {retry_count} of {retries}")
                time.sleep(5)  # Wait before retrying
        
        # Check if the timeout limit has been reached
        if i >= qc_timeout_limit:
            print(f"{os.path.basename(file_name)}: Error: RQC Execution took too long ({i} seconds)")
            print(f"{os.path.basename(file_name)}: Error: Execution took too long and is being RESTARTED")
            return execute_qc_and_get_response(stk_access_token, qc_slug, input_data, file_name, qc_timeout_limit)

def execute_qc_and_get_response(stk_access_token, qc_slug,input_data, file_name, qc_timeout_limit):
    execution_id = create_rqc_execution(qc_slug, stk_access_token, input_data, file_name)
    if execution_id:
        execution_status = get_execution_status(execution_id, stk_access_token,file_name, qc_timeout_limit, input_data, qc_slug)
        return execution_status
    else:
        return None
    
def process_file(file_name, file_code, stk_access_token, qc_slug, JIRA_API_TOKEN, qc_timeout_limit):
    print(f"Started processing file: {os.path.basename(file_name)}")
    
    if not file_code:  # Check if the file code is empty
        print(f"Skipping empty file: {file_name}")
        return

    response = None  # Initialize response to avoid referencing before assignment

    try:
        response = execute_qc_and_get_response(stk_access_token, qc_slug,file_code, file_name, qc_timeout_limit)
        #print(f"Raw response from StackSpot AI: {response}") # Log the raw response
    except Exception as e:
        print(f"Error processing file {file_name}: {e}")
        if response:
            print(f'This was the response from Stackspot AI: {response}')
        else:
            print(f"No response received from Stackspot AI for file {file_name}, and the RESPONSE FROM STK AI WAS: {response}")
        return

    print(f"{os.path.basename(file_name)} has been PROCESSED")
    
    # Assuming step 3 represents if it is secure (true) or unsecure (false)
    issue_dict = process_api_response_to_issue_dict(response, os.path.basename(file_name))
    
    for title, body in issue_dict.items():
        create_jira_issue(title, body, JIRA_API_TOKEN, file_name)



def sanitize_code(code):
    # Remove comments and strip extra whitespace
    sanitized_lines = []
    for line in code.split('\n'):
        # Remove comments
        line = line.split('#')[0]
        # Strip extra whitespace
        line = line.strip()
        if line:  # Only add non-empty lines
            sanitized_lines.append(line)
    return '\n'.join(sanitized_lines)


def create_github_issue(repo_owner, repo_name, title, body, gh_access_token):
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues"
    headers = {
        "Authorization": f"token {gh_access_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "title": title,
        "body": body
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx and 5xx)
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
        print(response.json())
        return None
    except Exception as err:
        print(f"An error occurred: {err}")
        return None
    else:
        print("Github issue created successfully.")
        return response.json()
    
def is_file_allowed(file_name, yaml_file):
    """ Load rules from a YAML file and check if a file is allowed based on the rules. """
    with open(yaml_file, "r") as file:
        rules = yaml.safe_load(file)
    return (file_name in rules['explicit_allow_list'] or any(file_name.endswith(ext) for ext in rules['implicit_allow_extensions'])) and \
           file_name not in rules['explicit_deny_list']

def read_select_files_in_repo(repo_path):
    """ Reads and selects files in the repository based on YAML rules. """
    # Get the GitHub workspace directory
    github_workspace = os.getenv('GITHUB_WORKSPACE', repo_path)
    
    # Path to the YAML file containing the rules
    yaml_file = os.path.join(github_workspace, 'scripts/define-scannable-files.yaml')
    
    code_dict = {}
    
    # Walk through the repository directory
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            # Check if the file is allowed based on the YAML rules
            if not is_file_allowed(file, yaml_file):
                continue
            
            print(f'THIS FILE WILL BE ANALYZED: {file}')
            
            # Read the file content and store it in the dictionary
            file_path = os.path.join(root, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                code_dict[file_path] = sanitize_code(f.read())
    
    return code_dict

def create_jira_issue(issue_title, issue_description, JIRA_API_TOKEN,file_name):
    JIRA_INSTANCE_URL = 'https://stackspot-sales-us.atlassian.net'
    USERNAME = 'lucas.vicenzotto@stackspot.com'
    PROJECT_KEY = 'POC'
    # Get the API token from the environment variable
    if not JIRA_API_TOKEN:
        raise ValueError("JIRA_API_TOKEN environment variable not set")
    url = f"{JIRA_INSTANCE_URL}/rest/api/2/issue"
    auth = HTTPBasicAuth(USERNAME, JIRA_API_TOKEN)
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "fields": {
            "project": {
                "key": PROJECT_KEY
            },
            "summary": issue_title,
            "description": issue_description,
            "issuetype": {
                "name": "Task"
            }
        }
    }
    response = requests.post(url, headers=headers, auth=auth, data=json.dumps(payload))
    if response.status_code == 201:
        print("Jira issue created successfully.")
        jira_issue = response.json()
        issue_key = jira_issue.get('key')
        
        # Construct the Jira issue URL
        jira_issue_url = f"{JIRA_INSTANCE_URL}/browse/{issue_key}"
        print(f"{os.path.basename(file_name)} Jira Issue URL: {jira_issue_url}")
        
        return jira_issue
    else:
        # Handle failure to create the issue
        print(f"Failed to create Jira issue. Status code: {response.status_code}")
        print(response.text)
        return None

def get_pull_request_files(repo_owner, repo_name, pull_number, github_token):#This function is not finished
    # GitHub API URL to get the list of files in a pull request
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pull_number}/files"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"Failed to fetch pull request files: {response.status_code} {response.text}")
    
    files = response.json()
    print(f'These are the files from the PR: {files}')
    code_dict = {}
    
    for file in files:
        file_name = file['filename']
        # Construct the raw URL manually
        raw_url = f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{file['sha']}/{file_name}"
        print(f"Fetching content from: {raw_url}")
        
        file_response = requests.get(raw_url, headers=headers)
        if file_response.status_code != 200:
            raise Exception(f"Failed to fetch file content for {file_name}: {file_response.status_code} {file_response.text}")
        
        file_code = file_response.text
        code_dict[file_name] = file_code
    
    return code_dict

def get_last_pull_request_number(repo_owner, repo_name, github_token):#This function is not finished
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls?state=all&sort=created&direction=desc"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {github_token}"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    pull_requests = response.json()
    if pull_requests:
        return pull_requests[0]['number']  # Return the number of the most recent pull request
    else:
        return None

def process_api_response_to_issue_dict(response, file_name):
    # Execute the Quick Command and get the response
    unfiltered_result = response.get('result')

    try:
        # Remove leading and trailing backticks and "json" tag if present
        if unfiltered_result.startswith("```json"):
            unfiltered_result = unfiltered_result[7:-4].strip()
        result_list = json.loads(unfiltered_result)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON format: {e}")
    i=0
    issue_dict={}
    for body in result_list:
        i+=1
        title=f'Issue #{i} in file: {os.path.basename(file_name)}'
        issue_dict[title]=body
    return issue_dict
def is_file_allowed(file_name, yaml_file):
    """ 
    Load rules from a YAML file and check if a file is allowed based on the rules.
    
    :param file_name: The name of the file to check.
    :param yaml_file: The path to the YAML file containing the rules.
    :return: True if the file is allowed, False if denied.
    """
    with open(yaml_file, "r") as file:
        rules = yaml.safe_load(file)
    
    return (file_name in rules['explicit_allow_list'] or 
            any(file_name.endswith(ext) for ext in rules['implicit_allow_extensions'])) and \
           file_name not in rules['explicit_deny_list']