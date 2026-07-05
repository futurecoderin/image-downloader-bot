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

def resolve_redirects(url: str) -> str:
    """Resolve redirection URLs (like facebook.com/share/r/) to their absolute destination using curl."""
    try:
        cmd = [
            "curl", "-s", "-I", "-L",
            "-o", "/dev/null",
            "-w", "%{url_effective}",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            resolved = result.stdout.strip()
            logger.info(f"Resolved redirect: {url} -> {resolved}")
            return resolved
    except Exception as e:
        logger.warning(f"Failed resolving redirect for {url}: {e}")
    return url

def fetch_html_with_curl(url: str) -> str:
    """Fetch webpage HTML using curl to bypass HTTP/1.1 and TLS blocks."""
    headers = [
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.5"
    ]
    cmd = ["curl", "-s", "-L"] + headers + [url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    if result.returncode == 0:
        return result.stdout
    raise RuntimeError(f"curl failed with code {result.returncode}: {result.stderr}")

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
    """Download a direct image URL using requests, falling back to curl if needed."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # Try downloading with requests
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=20)
        r.raise_for_status()
        
        parsed = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed.path)
        if not filename or not filename.lower().endswith(IMAGE_EXTENSIONS):
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
    except Exception as e:
        logger.warning(f"requests download failed for {url}: {e}. Falling back to curl...")
        
        # Fallback to curl download
        parsed = urllib.parse.urlparse(url)
        filename = os.path.basename(parsed.path) or f"image_{hash(url)}[:10].jpg"
        if not filename.lower().endswith(IMAGE_EXTENSIONS):
            filename += ".jpg"
        filename = clean_filename(filename)
        filepath = os.path.join(output_dir, filename)
        
        cmd = [
            "curl", "-s", "-L",
            "-o", filepath,
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return filepath
        raise RuntimeError(f"Failed to download image via requests or curl: {e}")

def download_with_gallery_dl(url: str, output_dir: str) -> list:
    """
    Download images using gallery-dl CLI. Resolves URLs first with -g to prevent download loops.
    Returns list of paths to downloaded files.
    """
    logger.info(f"Attempting gallery-dl link resolution for: {url}")
    
    # 1. Run gallery-dl -g to extract direct image links (restricted to first 5 matches)
    cmd_resolve = [
        "gallery-dl",
        "--range", "1-5",
        "-g",
        url
    ]
    try:
        result = subprocess.run(cmd_resolve, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("http")]
            if lines:
                logger.info(f"Resolved {len(lines)} direct URLs via gallery-dl -g. Downloading them...")
                downloaded_files = []
                for idx, direct_url in enumerate(lines):
                    try:
                        filepath = download_direct_image(direct_url, output_dir)
                        downloaded_files.append(filepath)
                    except Exception as download_err:
                        logger.error(f"Failed downloading resolved URL {direct_url}: {download_err}")
                if downloaded_files:
                    return downloaded_files
    except Exception as resolve_err:
        logger.error(f"gallery-dl resolution error: {resolve_err}")

    # 2. Fallback to standard gallery-dl download mode if resolution failed/empty
    logger.info("Falling back to standard gallery-dl download mode...")
    cmd_download = [
        "gallery-dl",
        "--range", "1-5",
        "--dest", output_dir,
        url
    ]
    try:
        result = subprocess.run(cmd_download, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            downloaded_files = []
            for root, _, files in os.walk(output_dir):
                for file in files:
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
    Uses curl fallback to bypass HTTP/1.1 TLS handshake blocks.
    """
    html_content = ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        html_content = r.text
    except Exception as req_err:
        logger.warning(f"requests HTML fetch failed ({req_err}). Trying curl fallback...")
        try:
            html_content = fetch_html_with_curl(url)
        except Exception as curl_err:
            raise RuntimeError(f"Failed fetching page with requests or curl: {curl_err}")

    soup = BeautifulSoup(html_content, 'html.parser')
    img_tags = soup.find_all('img')
    
    if not img_tags:
        raise ValueError("No images found on this web page.")
        
    candidates = []
    
    for img in img_tags:
        src = img.get('src')
        srcset = img.get('srcset')
        
        if srcset:
            parts = srcset.split(',')
            if parts:
                last_part = parts[-1].strip().split(' ')
                if last_part:
                    src = last_part[0]
                    
        if not src:
            continue
            
        absolute_url = urllib.parse.urljoin(url, src)
        lowercase_url = absolute_url.lower()
        if any(term in lowercase_url for term in ['icon', 'logo', 'avatar', 'spacer', 'ad-', '/ad/', 'sprite', 'pixel']):
            continue
            
        width = img.get('width')
        height = img.get('height')
        score = 0
        
        try:
            if width and height:
                w, h = int(width), int(height)
                if w < 100 or h < 100:
                    continue
                score = w * h
        except ValueError:
            pass
            
        if any(kw in lowercase_url for kw in ['original', 'full', 'large', 'hi-res', 'highres', 'wp-content/uploads']):
            score += 500000
            
        candidates.append((absolute_url, score))
        
    # Fallback: Check for raw Facebook CDN links in scripts if no candidates were found
    if not candidates:
        logger.info("No candidates found via soup. Trying direct Facebook CDN regex match...")
        fbcdn_matches = re.findall(r'https://scontent\.[a-zA-Z0-9\-]+\.fna\.fbcdn\.net/v/[^\s"\'\\>]+', html_content)
        for match in fbcdn_matches:
            # Clean HTML escape characters and JSON escapes
            clean_url = match.replace("&amp;", "&").replace("\\", "")
            if clean_url not in [c[0] for c in candidates]:
                candidates.append((clean_url, 100))
                
    if not candidates:
        raise ValueError("Could not find any suitable images on this page.")
        
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    errors = []
    for candidate_url, score in candidates[:5]:
        try:
            logger.info(f"Downloading HTML image candidate: {candidate_url} (Score: {score})")
            filepath = download_direct_image(candidate_url, output_dir)
            
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

def extract_facebook_mobile_image(url: str, output_dir: str) -> str:
    """Extract the main image of a Facebook post by requesting the mobile layout and scraping meta tags."""
    # Convert domain to mobile layout
    parsed_url = urllib.parse.urlparse(url)
    url = url.replace(parsed_url.netloc, "m.facebook.com")
    
    # Fetch page HTML with mobile browser headers
    headers = [
        "-H", "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.5"
    ]
    cmd = ["curl", "-s", "-L"] + headers + [url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed with code {result.returncode}: {result.stderr}")
        
    html_content = result.stdout
    
    # Look for fbcdn meta links using regex (very fast and robust)
    matches = re.findall(r'<meta[^>]+content="([^"]+)"', html_content)
    for match in matches:
        if 'fbcdn.net' in match:
            direct_url = match.replace('&amp;', '&').replace('\\', '')
            logger.info(f"Extracted Facebook mobile CDN image: {direct_url}")
            return download_direct_image(direct_url, output_dir)
            
    # Soup fallback
    soup = BeautifulSoup(html_content, 'html.parser')
    og_img = soup.find('meta', property='og:image')
    tw_img = soup.find('meta', name='twitter:image')
    
    for tag in [og_img, tw_img]:
        if tag and tag.get('content'):
            direct_url = tag.get('content').replace('&amp;', '&').replace('\\', '')
            if 'fbcdn.net' in direct_url:
                logger.info(f"Extracted Facebook mobile soup image: {direct_url}")
                return download_direct_image(direct_url, output_dir)
                
    raise ValueError("No Facebook CDN link found in mobile page HTML meta tags.")

def download_facebook_via_json(url: str, output_dir: str) -> list:
    """Download Facebook post images by parsing gallery-dl's JSON output."""
    import json
    logger.info(f"Using Facebook JSON extractor for: {url}")
    
    # Run gallery-dl in JSON metadata mode, restricted to a max range of 30 images to prevent infinite album crawling
    cmd = [
        "gallery-dl",
        "--range", "1-30",
        "-j",
        url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
        if result.returncode != 0:
            logger.warning(f"gallery-dl JSON extraction failed with code {result.returncode}. Stderr: {result.stderr}")
            return []
            
        data = json.loads(result.stdout)
        
        # Parse set metadata block (type 2 block) and image blocks (type 3 blocks)
        set_meta = None
        images = []
        
        for item in data:
            if not isinstance(item, list) or len(item) < 2:
                continue
            block_type = item[0]
            block_data = item[1]
            
            if block_type == 2:
                set_meta = block_data
            elif block_type == 3:
                img_url = item[1]
                img_meta = item[2] if len(item) > 2 else {}
                images.append((img_url, img_meta))
                
        if not set_meta or not images:
            logger.warning("No set metadata or images extracted from Facebook JSON.")
            return []
            
        set_id = set_meta.get("set_id", "")
        first_photo_id = set_meta.get("first_photo_id", "")
        
        downloaded_files = []
        
        if set_id.startswith("pcb."):
            # Multi-image post collection! Download all resolved images
            logger.info(f"Detected Facebook multi-image post collection ({set_id}). Downloading all images...")
            for img_url, img_meta in images:
                try:
                    filepath = download_direct_image(img_url, output_dir)
                    downloaded_files.append(filepath)
                except Exception as e:
                    logger.error(f"Failed downloading post image {img_url}: {e}")
        else:
            # Single-image post album! Return empty to fallback to mobile layout scraper for 100% accuracy
            logger.info(f"Detected Facebook single-image post ({set_id}). Falling back to mobile layout meta scraper...")
            return []
            
        return downloaded_files
        
    except Exception as e:
        logger.error(f"Error in download_facebook_via_json: {e}")
        return []

def download_image(url: str, output_dir: str) -> list:
    """
    Main entry point for downloading image(s) from a URL.
    Resolves redirects and handles gallery and single image paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Clean URL
    url = url.strip()
    
    # 2. Resolve redirects first (crucial for share links like facebook.com/share/r/)
    url = resolve_redirects(url)
    
    # 3. Handle Facebook URLs
    if "facebook.com" in url.lower():
        # First try: high-precision JSON parsing (handles both pcb multi-posts and single-image posts perfectly)
        try:
            filepaths = download_facebook_via_json(url, output_dir)
            if filepaths:
                return filepaths
        except Exception as json_err:
            logger.warning(f"Facebook JSON crawler failed: {json_err}. Trying mobile meta scraper...")
            
        # Second try: mobile layout meta scraper fallback (handles single images)
        try:
            logger.info(f"Using Facebook mobile meta scraper fallback for: {url}")
            filepath = extract_facebook_mobile_image(url, output_dir)
            if filepath:
                return [filepath]
        except Exception as fb_err:
            logger.warning(f"Facebook mobile scraper failed: {fb_err}. Falling back to general engines...")
            
    # 4. Check if direct image link
    if is_direct_image_url(url):
        logger.info(f"Detected direct image URL: {url}")
        filepath = download_direct_image(url, output_dir)
        return [filepath]
        
    # 5. Try downloading via gallery-dl
    files = download_with_gallery_dl(url, output_dir)
    if files:
        logger.info(f"Successfully downloaded {len(files)} files via gallery-dl")
        return files
        
    # 6. Fallback: Parse webpage HTML for best image
    logger.info(f"gallery-dl failed or unsupported. Scraping HTML for images: {url}")
    filepath = extract_best_image_from_html(url, output_dir)
    return [filepath]
