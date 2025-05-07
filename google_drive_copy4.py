from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.http import BatchHttpRequest
from concurrent.futures import ThreadPoolExecutor
import re
import os
import time
import random

SCOPES = ['https://www.googleapis.com/auth/drive']
source_folder_url = input('請輸入來源 Google Drive 資料夾網址:\n')
destination_folder_url = input('請輸入目標 Google Drive 資料夾網址:\n')

def authenticate():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def refresh_credentials(creds):
    try:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
            print("Token 已刷新")
        return creds
    except RefreshError as e:
        print(f"Token 刷新失敗: {e}. 重新進行認證...")
        creds = authenticate()
        return creds

def retry_request(creds, request_func, *args, **kwargs):
    max_retries = 5
    for attempt in range(max_retries):
        if creds.expired and creds.refresh_token:
            creds = refresh_credentials(creds)
        try:
            service = build('drive', 'v3', credentials=creds)
            request = request_func(service, *args, **kwargs)
            return request.execute(), creds
        except (HttpError) as e:
            if attempt == max_retries - 1:
                raise e
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            print(f"請求失敗: {e}. 將在 {wait_time:.2f} 秒後重試...")
            time.sleep(wait_time)
        except RefreshError:
            creds = refresh_credentials(creds)
    raise Exception("超過最大重試次數")

def batch_copy_files(service, files, destination_folder_id, creds):
    def callback(request_id, response, exception):
        if exception:
            print(f"批次複製失敗: {exception}")
        else:
            print(f"已複製檔案: {files[int(request_id)]['name']}")
    
    batch = service.new_batch_http_request(callback=callback)
    for i, file in enumerate(files):
        batch.add(service.files().copy(
            fileId=file['id'],
            body={'name': file['name'], 'parents': [destination_folder_id]},
            supportsAllDrives=True
        ), request_id=str(i))
    batch.execute()
    return creds

def extract_folder_id(url):
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None

def cache_folder_structure(service, folder_id, creds, cache=None):
    if cache is None:
        cache = []
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        response, creds = retry_request(
            creds,
            lambda s: s.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, parents)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageToken=page_token,
                pageSize=1000
            )
        )
        items = response.get('files', [])
        cache.extend(items)
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                cache, creds = cache_folder_structure(service, item['id'], creds, cache)
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return cache, creds

def copy_folder_recursive(service, source_folder_id, destination_parent_id, total_items, processed_items, creds, cache, is_top_level=True):
    folder_metadata, creds = retry_request(
        creds,
        lambda s: s.files().get(
            fileId=source_folder_id,
            fields='name',
            supportsAllDrives=True
        )
    )
    folder_name = folder_metadata['name']

    new_folder, creds = retry_request(
        creds,
        lambda s: s.files().create(
            body={
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [destination_parent_id]
            },
            fields='id',
            supportsAllDrives=True
        )
    )
    new_folder_id = new_folder['id']
    print(f"資料夾已複製：{folder_name}")

    # 篩選當前資料夾的子項目
    items = [item for item in cache if source_folder_id in item.get('parents', [])]
    files_to_copy = [item for item in items if item['mimeType'] != 'application/vnd.google-apps.folder']
    folders_to_copy = [item for item in items if item['mimeType'] == 'application/vnd.google-apps.folder']

    # 批次複製檔案
    if files_to_copy:
        creds = batch_copy_files(service, files_to_copy, new_folder_id, creds)
        processed_items += len(files_to_copy)
        if processed_items % 100 == 0:
            progress = (processed_items / total_items * 100) if total_items > 0 else 0
            print(f"進度: {progress:.2f}% ({processed_items}/{total_items})")

    # 遞迴處理子資料夾
    for folder in folders_to_copy:
        processed_items, creds = copy_folder_recursive(
            service, folder['id'], new_folder_id, total_items, processed_items, creds, cache, is_top_level=False
        )

    if is_top_level:
        return processed_items, new_folder_id, creds
    else:
        return processed_items, creds

def main():
    creds = authenticate()
    service = build('drive', 'v3', credentials=creds)
    
    source_folder_id = extract_folder_id(source_folder_url)
    if not source_folder_id:
        print("無法擷取來源資料夾 ID")
        return
    
    destination_folder_id = extract_folder_id(destination_folder_url)
    if not destination_folder_id:
        print("無法擷取目標資料夾 ID")
        return
    
    print("正在快取來源資料夾結構...")
    cache, creds = cache_folder_structure(service, source_folder_id, creds)
    total_items = len(cache)
    print(f"來源資料夾共有 {total_items} 個項目")
    
    print("開始複製資料夾...")
    processed_items, new_folder_id, creds = copy_folder_recursive(service, source_folder_id, destination_folder_id, total_items, 0, creds, cache)
    
    print("正在驗證複製結果...")
    if new_folder_id:
        cache, creds = cache_folder_structure(service, new_folder_id, creds)
        copied_items = len(cache)
        print(f"目標資料夾內新資料夾共有 {copied_items} 個項目")
        if copied_items == total_items:
            print("驗證成功：來源與目標資料夾項目數量一致！")
        else:
            print(f"驗證失敗：來源有 {total_items} 個項目，目標有 {copied_items} 個項目")
    else:
        print("驗證失敗：未找到新建立的目標資料夾")

    print("所有檔案與資料夾處理完成！")

if __name__ == '__main__':
    main()
