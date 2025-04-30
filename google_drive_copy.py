from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import re
import os

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

def copy_folder_recursive(service, source_folder_id, destination_parent_id):
    # 取得資料夾名稱
    folder_metadata = service.files().get(
        fileId=source_folder_id,
        fields='name',
        supportsAllDrives=True
    ).execute()
    folder_name = folder_metadata['name']

    # 建立目標資料夾
    new_folder = service.files().create(
        body={
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [destination_parent_id]
        },
        fields='id',
        supportsAllDrives=True
    ).execute()
    new_folder_id = new_folder['id']
    print(f"資料夾已複製：{folder_name}")

    # 找出所有子項目
    query = f"'{source_folder_id}' in parents and trashed = false"
    items = service.files().list(
        q=query,
        fields="files(id, name, mimeType)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute().get('files', [])

    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            # 遞迴處理子資料夾
            copy_folder_recursive(service, item['id'], new_folder_id)
        else:
            # 複製檔案
            copied_file = {
                'name': item['name'],
                'parents': [new_folder_id]
            }
            service.files().copy(
                fileId=item['id'],
                body=copied_file,
                supportsAllDrives=True
            ).execute()
            print(f"已複製檔案：{item['name']}")

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
    
    # 在指定目標資料夾中建立副本
    copy_folder_recursive(service, source_folder_id, destination_folder_id)
    print("所有檔案與資料夾皆已成功複製！")

if __name__ == '__main__':
    main()
