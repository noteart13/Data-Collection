import os
import pathlib
import mimetypes
from google.cloud import storage # pip install google-cloud-storage
import base64
import hmac
import hashlib
import requests
from io import BytesIO
import aiohttp
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential
import re
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
STORAGE_CLASSES = ('STANDARD', 'NEARLINE', 'COLDLINE', 'ARCHIVE')
IMAGEPROXY_HEX = os.getenv("IMGPROXY_KEY_HEX", "")
IMAGEPROXY_SALT = os.getenv("IMGPROXY_SALT_HEX", "")
proxy_host = os.getenv("IMGPROXY_HOST", "https://imgproxy.yourdomain.com")
proxy_path = os.getenv("IMGPROXY_PATH", "rs:fit:::0/g:no")

def urlsafe_base64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')
def url_to_hex(url):
    return url.encode('utf-8').hex()
def gcs_to_imgproxy_url(gcs_url, proxy_host, proxy_path, key_hex, salt_hex):
    if gcs_url.startswith('gs://'):
        gcs_url = gcs_url[5:]
    gcs_full = f'gs://{gcs_url}'
    encoded = urlsafe_base64_encode(gcs_full.encode())
    ext = 'jpg'
    
    path_to_sign = f'/{proxy_path}/{encoded}.{ext}'
    
    key = bytes.fromhex(key_hex)
    salt = bytes.fromhex(salt_hex)
    
    sig = hmac.new(key, salt + path_to_sign.encode(), hashlib.sha256).digest()
    sig_b64 = urlsafe_base64_encode(sig)
    
    return f"{proxy_host}/{sig_b64}{path_to_sign}"

async def normalize_address(address):
    address = address.lower()
    address = re.sub(r'[,\s]+', '_', address)
    address = re.sub(r'[^a-z0-9_]', '', address)
    return address.strip('_')
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
async def fetch_image(session, url):
    """Retry install image"""
    async with session.get(url, timeout=30) as response:
        response.raise_for_status()
        return await response.read()
class GCStorage:
    def __init__(self, storage_client, bucket_name):
        self.client = storage_client
        self.bucket = self.get_bucket(bucket_name)
    def create_bucket(self, bucket_name, storage_class, bucket_location='US'):
        bucket = self.client.bucket(bucket_name)
        bucket.storage_class = storage_class
        return self.client.create_bucket(bucket, bucket_location)        

    def get_bucket(self, bucket_name):
        return self.client.get_bucket(bucket_name)

    def list_buckets(self):
        buckets = self.client.list_buckets()
        return [bucket.name for bucket in buckets]
    def check_gcs_file_exists(self,gcs_path: str) -> bool:
        if not gcs_path.startswith("gs://"):
            raise ValueError("Invalid GCS path")

        # Parse path
        path = gcs_path[5:]
        parts = path.split("/", 1)

        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        if not blob_name:
            return False  

        
        blob = self.bucket.blob(blob_name)
        print(blob.exists())
        return blob.exists()
    async def upload_images_to_gcs(self, image_url, address, property_id="properties/prosale"):
        safe_address = await normalize_address(address)
        
        async with aiohttp.ClientSession() as session:
            try:
                content = await fetch_image(session, image_url)
                image_bytes = BytesIO(content)
                

                ext = "jpg"
                try:
                    with Image.open(image_bytes) as img:
                        img_format = img.format.lower() if img.format else "jpg"
                        ext = "jpg" if img_format == "jpeg" else img_format
                except Exception:
                    pass

    
                image_bytes.seek(0)
                url_hex = url_to_hex(image_url)
                blob_name = f"{property_id}/{safe_address}/{safe_address}_{url_hex}.{ext}"
                
                blob = self.bucket.blob(blob_name)
                
                # 4. Upload lên GCS
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, 
                    lambda: blob.upload_from_file(
                        image_bytes, 
                        content_type=f"image/{ext}"
                    )
                )

                return f"gs://{self.bucket.name}/{blob_name}"

            except Exception as e:
                print(f"[ERROR] {image_url}: {e}")
                return image_url
    def delete_images(self, images):
        """
        Delete list of images from GCS.
        images: list đường dẫn dạng gs://bucket/path
        """
        deleted = []
        errors = []

        for img in images:
            try:
                if img['url'].startswith("gs://"):
                    # remove gs://bucket/
                    blob_name = img['url'].replace(f"gs://{self.bucket.name}/", "")
                else:
                    blob_name = img['url']

                blob = self.bucket.blob(blob_name)
                blob.delete()

                deleted.append(img['url'])

            except Exception as e:
                print(f"Delete error {img['url']}: {e}")
                errors.append(img['url'])

        return {
            "deleted": deleted,
            "errors": errors
        }
    def list_blobs(self, bucket_name):
        return self.client.list_blobs(bucket_name)
        

bucket_name = os.getenv("GCS_BUCKET_NAME", "property-media-bucket")
gcs_key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
try:
    if os.path.exists(gcs_key_path):
        storage_client = storage.Client.from_service_account_json(gcs_key_path)
    else:
        storage_client = storage.Client()
except Exception:
    storage_client = None

gcs = GCStorage(storage_client, bucket_name) if storage_client else None





