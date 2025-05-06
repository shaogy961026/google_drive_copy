from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import re
import os
import time

SCOPES = ['https://www.googleapis.com/auth/drive']
source_folder_url = input('請輸入來源 Google Drive 資料夾網址:\n')
destination_folder_url = input('請輸入目標 Google Drive 資料夾網址:\n')

def extract_folder_id(url):
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None

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

def retry_request(request_func, *args, **kwargs):
    while True:
        try:
            return request_func(*args, **kwargs).execute()
        except HttpError as e:
            print(f"請求失敗: {e}. 將在30秒後重試...")
            time.sleep(30)

def count_all_items_recursive(service, folder_id):
    total_items = 0
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        response = retry_request(
            service.files().list,
            q=query,
            fields="nextPageToken, files(id, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token
        )
        items = response.get('files', [])
        total_items += len(items)
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                total_items += count_all_items_recursive(service, item['id'])
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return total_items

def copy_folder_recursive(service, source_folder_id, destination_parent_id, total_items=0, processed_items=0):
    # 取得資料夾名稱
    folder_metadata = retry_request(
        service.files().get,
        fileId=source_folder_id,
        fields='name',
        supportsAllDrives=True
    )
    folder_name = folder_metadata['name']

    # 建立目標資料夾
    new_folder = retry_request(
        service.files().create,
        body={
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [destination_parent_id]
        },
        fields='id',
        supportsAllDrives=True
    )
    new_folder_id = new_folder['id']
    print(f"資料夾已複製：{folder_name}")

    # 找出所有子項目
    query = f"'{source_folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        response = retry_request(
            service.files().list,
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token
        )
        items = response.get('files', [])

        for item in items:
            processed_items += 1
            progress = (processed_items / total_items * 100) if total_items > 0 else 0
            print(f"進度: {progress:.2f}% ({processed_items}/{total_items}) - 處理: {item['name']}")

            if item['mimeType'] == 'application/vnd.google-apps.folder':
                # 遞迴處理子資料夾
                processed_items = copy_folder_recursive(
                    service, item['id'], new_folder_id, total_items, processed_items
                )
            else:
                # 複製檔案
                copied_file = {
                    'name': item['name'],
                    'parents': [new_folder_id]
                }
                retry_request(
                    service.files().copy,
                    fileId=item['id'],
                    body=copied_file,
                    supportsAllDrives=True
                )
                print(f"已複製檔案：{item['name']}")

        page_token = response.get('nextPageToken')
        if not page_token:
            break

    return processed_items

def main():
    creds = authenticate()
    service = build('drive', 'v3', credentials=creds)
    
    # 提取來源資料夾 ID
    source_folder_id = extract_folder_id(source_folder_url)
    if not source_folder_id:
        print("無法擷取來源資料夾 ID")
        return
    
    # 提取目標資料夾 ID
    destination_folder_id = extract_folder_id(destination_folder_url)
    if not destination_folder_id:
        print("無法擷取目標資料夾 ID")
        return
    
    # 計算來源資料夾總項目數（包括子資料夾）
    print("正在計算來源資料夾項目數...")
    total_items = count_all_items_recursive(service, source_folder_id)
    print(f"來源資料夾共有 {total_items} 個項目")

    # 在指定目標資料夾中建立副本
    print("開始複製資料夾...")
    copy_folder_recursive(service, source_folder_id, destination_folder_id, total_items)

    # 檢查目標資料夾內新建立的資料夾項目數
    print("正在驗證複製結果...")
    query = f"'{destination_folder_id}' in parents and trashed = false"
    new_folders = retry_request(
        service.files().list,
        q=query,
        fields="files(id, mimeType)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).get('files', [])
    
    # 找到新建立的資料夾
    new_folder_id = None
    for folder in new_folders:
        if folder['mimeType'] == 'application/vnd.google-apps.folder':
            new_folder_id = folder['id']
            break
    
    if new_folder_id:
        copied_items = count_all_items_recursive(service, new_folder_id)
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
