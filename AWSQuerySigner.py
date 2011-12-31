# AWSQS = Amazon Web Services / Product Advertising API Query Signer
#
# Python AWS/PAA signing code.
#
# 2009.07.12, SowaCS: Changed "AWSQS.signedrequest" to use "canonicalquery" instead of urllib.urlencode original
# 2009.06.17, SowaCS: refactored
# 2009.05.31, SowaCS: original
#
#
#	All customary licenses and disclaimers for code posted in open forums apply.
#
#	============================================================================
#
#
#	Dependencies:
#
#	- originally developed for Python 2.5; ymmv for other versions
#
#
#	Usage:
#
#	- import in calling code: "from AWSQuerySigner import AWSQS"
#	- contructor: pass method, endpoint, uri, REST query params as dict, AWS secret key
#               example: awsqs = AWSQS( method, endpoint, uri, params, keyval )
#	- results:  signedrequest property (string): full signed request url
#               signedparams  property (dict)  : dict of params with timestamp & signature
#               example: handler.redirect( awsqs.signedrequest )
#
#
#	Notes:
#
#	- No error handling is provided

import hashlib, hmac, base64, urllib, time

#=====================================================#
	
TIMPARMNAME = 'Timestamp'
SIGPARMNAME = 'Signature'

#=====================================================#

class AWSQS:
	"""AWS Query Signer."""
	
	def __init__( self, method, endpoint, uri, params, keyval ):
		"""Calculate everything on instantiation.
		
			Stateful object.
		"""

		# operate on _copy_ of params
		query = dict( params )
		if TIMPARMNAME not in query:
			query[TIMPARMNAME] = now()
		if SIGPARMNAME in query:
			del query[SIGPARMNAME]

		# canonical query only used for signature calculation			
		canonicalquery = canonicalize( query )
		stringtosign = '\n'.join( [ method, endpoint, uri, canonicalquery  ] )
		rawsignature = base64.b64encode( hmac.new( keyval, stringtosign, hashlib.sha256 ).digest() )
		
		# update query dict with encoded signature
		kvSignature = urllib.urlencode({ SIGPARMNAME : rawsignature })
		query.update( [kvSignature.split('=')] )
		
		# _original_ parameters updated with timestamp & encoded signature info
		self.signedparams = query
		
		# canonical query has already done utf-8 conversion + url(re-)encoding
		# can't just use original querystring due to potential (re-)encoding issues
		# default urlencode & webapp location header setting use 'ascii' encoding
		querystring = canonicalquery + '&' + kvSignature
		self.signedrequest = 'http://' + endpoint + uri + '?' + querystring


#=====================================================#

def now():
	"""Get formatted timestamp."""
	return time.strftime( '%Y-%m-%dT%H:%M:%S.000Z', time.gmtime() )


def canonicalize( query ):
	"""Get sorted, encoded querystring from dict pairs."""
	pairs = [(k, (v.encode('utf-8'))) for (k, v) in query.iteritems()]
	pairs.sort()
	return urllib.urlencode( pairs ).replace( "%7E", "~" ).replace( "+", "%20" )
