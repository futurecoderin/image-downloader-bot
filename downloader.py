import os
import re
import urllib.parse
import subprocess
import logging
import requests
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

# List of common image extensions
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff')

def clean_filename(filename: str) -> str:
    """Sanitize the filename by removing invalid characters."""
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def is_direct_image_url(url: str) -> bool:
    """Check if the URL points directly to an image file."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    
    # Check extension
    if path.endswith(IMAGE_EXTENSIONS):
        return True
        
    # Check headers (HEAD request)
    try:
        r = requests.head(url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True, timeout=5)
        content_type = r.headers.get("Content-Type", "").lower()
        if content_type.startswith("image/"):
            return True
    except Exception as e:
        logger.warning(f"HEAD request failed for {url}: {e}")
        
    return False

def download_direct_image(url: str, output_dir: str) -> str:
    """Download a direct image URL using requests."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, stream=True, timeout=15)
    r.raise_for_status()
    
    # Try to extract filename from URL path
    parsed = urllib.parse.urlparse(url)
    filename = os.path.basename(parsed.path)
    if not filename or not filename.lower().endswith(IMAGE_EXTENSIONS):
        # Fallback filename
        content_type = r.headers.get("Content-Type", "")
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"
        elif "gif" in content_type:
            ext = "gif"
        filename = f"image_{int(r.elapsed.total_seconds() * 1000)}.{ext}"
        
    filename = clean_filename(filename)
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                
    return filepath

def download_with_gallery_dl(url: str, output_dir: str) -> list:
    """
    Download images using gallery-dl CLI.
    Returns list of paths to downloaded files.
    """
    logger.info(f"Attempting gallery-dl download for: {url}")
    # Run gallery-dl to download to output_dir
    # --dest controls the base destination path
    # --no-mtime maintains download time
    cmd = [
        "gallery-dl",
        "--dest", output_dir,
        url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            # Find all files in the output directory recursively
            downloaded_files = []
            for root, _, files in os.walk(output_dir):
                for file in files:
                    # Ignore temporary/system files
                    if file.lower().endswith(IMAGE_EXTENSIONS):
                        downloaded_files.append(os.path.join(root, file))
            return downloaded_files
        else:
            logger.warning(f"gallery-dl failed with code {result.returncode}. Stderr: {result.stderr}")
            return []
    except Exception as e:
        logger.error(f"gallery-dl execution error: {e}")
        return []

def extract_best_image_from_html(url: str, output_dir: str) -> str:
    """
    Scrape a website, analyze its <img> tags, and download the largest high-resolution image.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    
    soup = BeautifulSoup(r.text, 'html.parser')
    img_tags = soup.find_all('img')
    
    if not img_tags:
        raise ValueError("No images found on this web page.")
        
    candidates = []
    
    for img in img_tags:
        # Determine candidate src
        src = img.get('src')
        srcset = img.get('srcset')
        
        # If srcset exists, take the largest source URL (usually the last entry)
        if srcset:
            parts = srcset.split(',')
            if parts:
                last_part = parts[-1].strip().split(' ')
                if last_part:
                    src = last_part[0]
                    
        if not src:
            continue
            
        # Resolve relative URLs
        absolute_url = urllib.parse.urljoin(url, src)
        
        # Ignore obvious icons, small tracking images, ads, etc.
        lowercase_url = absolute_url.lower()
        if any(term in lowercase_url for term in ['icon', 'logo', 'avatar', 'spacer', 'ad-', '/ad/', 'sprite', 'pixel']):
            continue
            
        # Check attributes for sizing hints
        width = img.get('width')
        height = img.get('height')
        score = 0
        
        # Calculate sizing scores
        try:
            if width and height:
                w, h = int(width), int(height)
                if w < 100 or h < 100:  # Skip tiny thumbnails
                    continue
                score = w * h
        except ValueError:
            pass
            
        # Prioritize OpenGraph / high-res keywords in paths
        if any(kw in lowercase_url for kw in ['original', 'full', 'large', 'hi-res', 'highres', 'wp-content/uploads']):
            score += 500000  # Bonus score equivalent to ~700x700 px dimensions
            
        candidates.append((absolute_url, score))
        
    if not candidates:
        raise ValueError("Could not find any suitable high-resolution images on this page.")
        
    # Sort candidates by score descending
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # Try downloading candidates starting with the highest score
    errors = []
    for candidate_url, score in candidates[:5]:
        try:
            logger.info(f"Downloading HTML image candidate: {candidate_url} (Score: {score})")
            filepath = download_direct_image(candidate_url, output_dir)
            
            # Verify it's a valid image and check dimensions
            try:
                with Image.open(filepath) as img_check:
                    width, height = img_check.size
                    if width < 150 or height < 150:
                        os.remove(filepath)
                        raise ValueError(f"Image too small: {width}x{height}")
                return filepath
            except Exception as im_err:
                if os.path.exists(filepath):
                    os.remove(filepath)
                raise im_err
                
        except Exception as e:
            errors.append(f"Failed {candidate_url}: {e}")
            continue
            
    raise ValueError(f"Could not download any valid image. Details:\n" + "\n".join(errors))

def download_image(url: str, output_dir: str) -> list:
    """
    Main entry point for downloading image(s) from a URL.
    Returns a list of local file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Clean URL
    url = url.strip()
    
    # 2. Check if direct image link
    if is_direct_image_url(url):
        logger.info(f"Detected direct image URL: {url}")
        filepath = download_direct_image(url, output_dir)
        return [filepath]
        
    # 3. Try downloading via gallery-dl (social media / galleries)
    files = download_with_gallery_dl(url, output_dir)
    if files:
        logger.info(f"Successfully downloaded {len(files)} files via gallery-dl")
        return files
        
    # 4. Fallback: Parse webpage HTML for best image
    logger.info(f"gallery-dl failed or unsupported. Scraping HTML for images: {url}")
    filepath = extract_best_image_from_html(url, output_dir)
    return [filepath]
