#!/usr/bin/env python3

"""
linode_dyndns - Dynamic DNS updater for Linode-hosted DNS.

<https://github.com/joshdick/linode_dyndns>
"""

__author__ = 'Josh Dick <joshdick.net>'
__email__ = 'josh@joshdick.net'
__copyright__ = '(C) 2011, Josh Dick'
__license__ = 'Apache 2.0'

from urllib.parse import urlencode
from urllib.request import urlopen
import argparse, configparser, http.client, json, socket, sys

# Should point to a service on the Internet that returns the requester's IP address in plain text.
_IP_FINDER_URL = 'http://www.whatismyip.com/automation/n09230945.asp'

# Will be populated by main() from commandline arguments.
_LINODE_API_KEY = ''

def _handleError(message, ex=None):

  print('Error: ' + message)
  if (ex is not None):
    print('Root cause:')
    print('\t' + str(ex))
  print('Terminating.')
  sys.exit(-1)


# Bail out if the input isn't a valid IP address.
def _validateIP(ip):

  try:
    socket.inet_aton(ip)
  except socket.error as e:
    errorMessage = 'The supplied IP address "' + ip + '" is invalid.'
    _handleError(errorMessage, e)


# The only reliable way to determine one's true external IP address if they're behind a proxy or router.
def _getExternalIP():

  ip = ''
  errorMessage = 'Could not determine your external IP address.'

  # Try grabbing the external IP address from a service
  try:
    f = urlopen(_IP_FINDER_URL)
    ip = f.read().decode("utf-8").strip()
    f.close()
  except Exception as e:
    _handleError(errorMessage, e)

  _validateIP(ip)

  return ip


def _linodeAPICall(apiParams):

  # Build the Linode API request URI from the supplied dict
  apiParams['api_key'] = _LINODE_API_KEY
  requestURI = '/?' + urlencode(apiParams)

  # Attempt to make the API call
  response_raw = ''
  try:
    conn = http.client.HTTPSConnection("api.linode.com")
    conn.request("GET", requestURI)
    response_raw = conn.getresponse().read().decode("utf-8")
    conn.close()
  except Exception as e:
    _handleError('An error occurred while connecting to the Linode API.', e)

  # The API returns JSON by default - parse it
  response_obj = {}
  try:
    response_obj = json.loads(response_raw)
  except Exception as e:
    _handleError('Couldn\'t parse JSON response from Linode API.', e)

  # Bail out if an API call failed
  api_errors = response_obj['ERRORARRAY']
  if len(api_errors) > 0:
    errorMessage = 'The Linode API call failed with the following error(s).'
    for error in api_errors:
      errorMessage += '\n\t' + error['ERRORMESSAGE'] + ' (Error Code: ' + str(error['ERRORCODE']) + ')'
    _handleError(errorMessage)

  return response_obj


# Returns a Linode Domain ID.
# Given a Linode Domain ID, validate it. If valid, return it unmodified.
# Given a domain name, try to find an associated Linode Domain ID and return it if found.
def _normalizeDomainID(candidate):

  apiParams = {}
  apiParams['api_action'] = 'domain.list'

  apiResult = _linodeAPICall(apiParams)

  # The DATA array from the Linode domain.list action contains a list of domains
  for domain in apiResult['DATA']:
    domainID = str(domain['DOMAINID'])
    # If we were supplied a valid Domain ID or a valid domain name, return the corrseponding Domain ID.
    if (domainID == candidate or domain['DOMAIN'] == candidate):
      return domainID

  _handleError('The supplied Domain ID/domain name "' + candidate + '" is invalid or is not associated with the supplied Linode API key.')


# Returns a Linode Resource ID.
# Given a Linode Domain ID and Resource ID, validate the Resource ID. If valid, return it unmodified.
# Given a Linode Domain ID and subdomain/host name, try to find an associated Linode Resource ID and return it if found.
def _normalizeResourceID(domainID, candidate):

  apiParams = {}
  apiParams['api_action'] = 'domain.resource.list'
  apiParams['DomainID'] = domainID

  apiResult = _linodeAPICall(apiParams)

  # The DATA array from the Linode domain.resource.list action contains a list of resources (DNS records.)
  for resource in apiResult['DATA']:
    resourceType = resource['TYPE'].lower()
    resourceID = str(resource['RESOURCEID'])
    # Assume we can only use A records for dynamic DNS.
    # If we were supplied a valid Resource ID or a valid resource name, return the corresponding Resource ID.
    if (resourceType == 'a' and (resourceID == candidate or resource['NAME'] == candidate)):
      return resourceID

  _handleError('The supplied Resource ID/name "' + candidate + '" is invalid or is not associated with Domain ID "' + domainID + '".')


# Updates the given resource/domain to point to the given target (IP address.)
def _updateDynDNS(domainID, resourceID, target):

  apiParams = {}
  apiParams['api_action'] = 'domain.resource.update'
  apiParams['DomainID'] = domainID
  apiParams['ResourceID'] = resourceID
  apiParams['target'] = target

  _validateIP(target)

  # Succeed silently
  _linodeAPICall(apiParams)


def _main():

  scriptName = sys.argv[0]
  epilogText = 'Examples:'
  epilogText += '\n' + scriptName + ' 1A4FB63C245D domain.net subdomain'
  epilogText += '\n' + scriptName + ' -ip 1.2.3.4 1A4FB63C245D 123456 subdomain'

  parser = argparse.ArgumentParser(
    formatter_class = argparse.RawDescriptionHelpFormatter,
    description = 'Dynamic DNS updater for Linode-hosted DNS.',
    epilog = epilogText
  )
  parser.add_argument('apiKey', type=str, help='Linode API key.')
  parser.add_argument('domainID', type=str, help='The ID of the domain to update. Can be a hostname or a numeric Linode Domain ID.')
  parser.add_argument('resourceID', type=str, help='The ID of the resource to update. Must be associated with the supplied domain ID. Can be a hostname or a numeric Linode Resource ID.')
  parser.add_argument('-ip', type=str, required=False, help='The target IP address to update to. If omitted, will use the external IP address of the machine running this program.')

  args = parser.parse_args()

  # Store un-normalized copies of this information so that human-readable names will be saved to the cache, if names were supplied
  userDomainId = args.domainID
  userResourceId = args.resourceID

  global _LINODE_API_KEY;
  _LINODE_API_KEY = args.apiKey

  ip = ''
  if (args.ip is not None):
    ip = args.ip
  else:
    ip = _getExternalIP()

  # Try to read information about the last update for the reqeuested domain/resource from the cache,
  # so we don't have to waste Linode API calls
  cache_filename = 'linode_dyndns_cache.ini'
  cache = configparser.ConfigParser()

  # Attempt to read an existing cache file - this will fail silently if the file doesn't exist
  cache.read(cache_filename)

  cacheKeyName = userResourceId + '.' + userDomainId

  if (cacheKeyName in cache and 'ip' in cache[cacheKeyName] and cache[cacheKeyName]['ip'] == ip):
    print('The target IP address "' + ip + '" is already current for Resource ID "' + userResourceId + '" with Domain ID "' + userDomainId + '".')
    print('No update is needed.')
    print('Terminating.')
    sys.exit()

  domainId = _normalizeDomainID(userDomainId)
  resourceId = _normalizeResourceID(domainId, userResourceId)

  # The Linode API supports target=[remote_addr], but this doesn't work if the script is running behind a proxy.
  _updateDynDNS(domainId, resourceId, ip)

  cache[cacheKeyName] = {}
  cache[cacheKeyName]['ip'] = ip

  with open(cache_filename, 'w') as output_file:
    cache.write(output_file)

  print('The target IP address for Resource ID "' + userResourceId + '" with Domain ID "' + userDomainId + '" was successfully updated to "' + ip + '".')
  print('Terminating.')
  sys.exit()


if __name__ == '__main__':

  _main()
