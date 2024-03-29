import logging
import re
import sys
from bs4 import BeautifulSoup
from queue import Queue, PriorityQueue
from urllib import parse, request

logging.basicConfig(level=logging.DEBUG, filename='output.log', filemode='w')
visitlog = logging.getLogger('visited')
extractlog = logging.getLogger('extracted')


def parse_links(root, html):
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a'):
        href = link.get('href')
        if href:
            text = link.string
            if not text:
                text = ''
            text = re.sub('\s+', ' ', text).strip()
            yield (parse.urljoin(root, link.get('href')), text)

def parse_links_sorted(root, html):
    urls = []

    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a'):
        href = link.get('href')
        if href:
            text = link.string
            if not text:
                text = ''
            text = re.sub('\s+', ' ', text).strip()
            urls.append((parse.urljoin(root, link.get('href')), text))

    urls.sort(reverse=True, key=rank_link)
    
    for url in urls:
        yield(url)

# One of the main metrics used for ranking a link is how many '/' (subdirectories) it 
# contains. The logic behind this is that the more '/' present, the deeper into a domain
# a link is and therefore the more specific the content it is traversing will be.
# We assume that more specific content is more important to extract first.
# Additionally, this will allow for the crawler to go as deep as possible into the
# domain first, ultimately reaching the deepest parts first, and then working back
# up from there to the more generic parts.
# e.g., if the crawler explores www.cs.jhu.edu, the links it retrieves will be the
# upmost general--links applying to the entirety of computer science at jhu.
# But, if the crawler explores a more specific link before this, such as 
# https://www.cs.jhu.edu/~yarowsky/cs466.html, the content retrieved will be much more
# individulized (i.e., pertaining to a particular professor and one of their courses).
# This is excatly what we want the crawler to do: extract the information that is hard
# to reach from the generic domain which everyone already knows and can easily access.
#
# Another ranking metric is how long the link's text is.
# The assumption made is that the longer the text is, the more prominent on the
# webpage and thus more important a given link will be.
# Consider: if a given link was not important, it would contain as little text as 
# possible on the screen so as to not entice a user to click on it/draw attention to it.
# This metric is not weighted as heavily as the number of '/' because it often could 
# be the case that certain links are described using many words stylistically, not
# indicating any relevance to the importance of that link. Also, some expressions in English
# are much shorter than their equivalent in other languages, yet they mean the same thing:
# e.g., "I'm ten!" vs French "j'ai dix ans!" – they ought to be ranked the same despite char length)
#
# In short, the first metric listed above is universal to any language and does not
# deal with subjectivity, thus deserving a greater weighting. Nonetheless, the second
# metric is important for helping to settle rank ties and thus be more precise
# (e.g., it is common for links only to be a couple subdirectories from the domain).
def rank_link(link):
    rank = 0 # the higher the rank, the greater the priority to crawl 

    url = link[0]
    url = strip_http_request(url)

    num_subdirectories = 0
    for char in url:
        if char == '/':
            num_subdirectories += 1

    rank = 10 * num_subdirectories + len(link[1])

    return rank

def get_links(url):
    res = request.urlopen(url)
    return list(parse_links(url, res.read()))

def get_nonlocal_links(url):
    '''Get a list of links on the page specificed by the url,
    but only keep non-local links and non self-references.
    Return a list of (link, title) pairs, just like get_links()'''

    stripped_root_link = strip_http_request(url)

    links = get_links(url)
    filtered = []

    for link in links:
        if is_non_local(link[0], stripped_root_link):
            filtered.append(link)

    return filtered


def is_http_request(url):
    if len(url) > 8 and url[4] == 's':
        if url[0:8] == "https://":
            return True
    elif len(url) > 7:
        if url[0:7] == "http://":
            return True

def strip_http_request(url):
    if is_http_request(url) and url[4] == 's': # an https request
        return url[8:len(url)]
    elif is_http_request:
        return url[7:len(url)]
    
    return url # already stripped

def strip_www(url):
    if len(url) > 4 and url[0] == "w" and url[1] == "w" and url[2] == "w" and url[3] == ".":
        url = url[4:len(url)]
    
    return url

def get_domain(url):
    domain = ""

    stripped_url = strip_www(strip_http_request(url))

    i = 0
    while i < len(stripped_url) and stripped_url[i] != '/':
        domain += stripped_url[i]
        i += 1

    return domain

def is_non_local(url, stripped_root_link):
    if is_http_request(url):
        if strip_http_request(url) != stripped_root_link:
                return True
        
    return False


def crawl(root, wanted_content=[], within_domain=True):
    '''Crawl the url specified by `root`.
    `wanted_content` is a list of content types to crawl
    `within_domain` specifies whether the crawler should limit itself to the domain of `root`
    '''
    root_domain = get_domain(root)

    queue = Queue()
    queue.put(root)

    visited = []
    extracted = []

    content_types = {
        'text': ["text/html; charset=UTF-8", "text/plain; charset=UTF-8"],
        'html': ["text/html; charset=UTF-8"],
        'pdf:': ["application/pdf"],
        'zip:': ["application/zip"],
        'jpeg': ["image/jpeg"],
        'png': ["image/png"],
        'pptx': ["application/vnd.ms-powerpoint"]
    }

    while not queue.empty():
        url = queue.get()
        try:
            req = request.urlopen(url)
            html = req.read()
            headers = req.headers['Content-Type']

            if len(wanted_content) > 0: # check if user wanted specific type(s) of content

                content_matches = False
                for content in wanted_content: # if so, determine if one is the same as the req header
                    for appropriate_header in content_types[content.lower()]:
                        if appropriate_header == headers:
                            content_matches = True

                if not content_matches:
                    continue

            visited.append(url)
            visitlog.debug(url)

            for ex in extract_information(url, html):
                extracted.append(ex)
                extractlog.debug(ex)

            stripped_root_link = strip_http_request(url)

            links_added_to_queue = [] # prevents repeat links being added

            for link, title in parse_links_sorted(url, html):

                if link not in links_added_to_queue:

                    if is_non_local(link, stripped_root_link):

                        if link not in visited:

                            if not within_domain or get_domain(link) == root_domain:
                                links_added_to_queue.append(link)
                                queue.put(link)

        except Exception as e:
            print(e, url)

    return visited, extracted


def extract_information(address, html):
    '''Extract contact information from html, returning a list of (url, category, content) pairs,
    where category is one of PHONE, ADDRESS, EMAIL'''
    html = BeautifulSoup(html, "html.parser")

    results = []

    for match in re.findall('\d\d\d-\d\d\d-\d\d\d\d', str(html)):
        results.append((address, 'PHONE', match))
    
    # account for other formating
    for match in re.findall('\(\d\d\d\) \d\d\d-\d\d\d\d', str(html)):
        results.append((address, 'PHONE', match))
    
    # hypens, periods, and underscores are all valid special characters besides alphanumerics for an email's username and domain; 
    # domain extension must contain a '.' followed by a 2-3 digit long sequence, e.g., '.us' '.edu' '
    for match in re.findall('([a-zA-Z0-9_\-\.]+@[a-zA-Z0-9_\-\.]+\.[a-zA-Z]{2,3})', str(html)): 
        results.append((address, 'EMAIL', match))

    for match in re.findall('[a-zA-Z]+ ?[a-zA-z]+?, [a-zA-Z.]+ [0-9]{5}', str(html)):
        results.append((address, 'ADDRESS', match))

    return results


def writelines(filename, data):
    with open(filename, 'w') as fout:
        for d in data:
            print(d, file=fout)


def main():
    site = sys.argv[1]

    links = get_links(site)
    writelines('links.txt', links)

    nonlocal_links = get_nonlocal_links(site)
    writelines('nonlocal.txt', nonlocal_links)

    visited, extracted = crawl(site, wanted_content=["HTML"], within_domain=True)
    writelines('visited.txt', visited)
    writelines('extracted.txt', extracted)


if __name__ == '__main__':
    main()