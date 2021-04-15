#!/usr/bin/env python3
# Copyright (c) 2021 Jarret Dyrbye
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php

import time
import traceback
import logging

from flask import Flask, render_template, request, redirect
from flask_login import current_user, login_user, logout_user

from gunicorn.app.base import BaseApplication


from moneysocket.wad.wad import Wad
from moneysocket.beacon.beacon import MoneysocketBeacon
from moneysocket.beacon.location.websocket import WebsocketLocation

from models import UserModel, AccountAssignment, db, login
from config import read_config
from terminus_rpc import TerminusRpc


###############################################################################
# read config file
###############################################################################

config = read_config()

###############################################################################
# rpc connection
###############################################################################

rpc = TerminusRpc(config)


###############################################################################
# setup flask and db
###############################################################################

app = Flask(__name__)
app.secret_key = config['Db']['SecretKey']
app.config['SQLALCHEMY_DATABASE_URI'] = ("sqlite:////" +
                                         config['Db']['DatabaseFile'])
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
login.init_app(app)
login.login_view = 'login'
db.init_app(app)


app.static_folder ='static'

###############################################################################
# database initialization
###############################################################################

@app.before_first_request
def create_table():
    logging.info("create table")
    db.create_all()

###############################################################################
# database helpers
###############################################################################

def user_accounts():
    return [aa.account_name for aa in current_user.account_assignments]

###############################################################################
# app rpcs
###############################################################################

def getaccountinfo_rpc():
    try:
        ua = user_accounts()
        info = rpc.call(['getaccountinfo'] + ua)
        accounts = info['accounts']
        for account in accounts:
            account['wad'] = str(Wad.from_dict(account['wad']))
            account['cap'] = str(Wad.from_dict(account['cap']))
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(e)
        return {'success': False, 'error': "RPC exception"}
    return accounts

def getaccountreceipts_rpc(account):
    try:
        info = rpc.call(['getaccountreceipts', account])
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(e)
        return {'success': False, 'error': "RPC exception"}
    return info

def connect_rpc(account, beacon):
    try:
        info = rpc.call(['connect', account, beacon])
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(e)
        return {'success': False, 'error': "RPC exception"}
    return info

def clear_rpc(account):
    try:
        info = rpc.call(['clear', account])
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(e)
        return {'success': False, 'error': "RPC exception"}
    return info

def rm_rpc(account):
    try:
        info = rpc.call(['rm', account])
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(e)
        return {'success': False, 'error': "RPC exception"}
    return info

def create_rpc(username):
    cap = config['Account']['Cap']
    start = config['Account']['StartBalance']
    try:
        info = rpc.call(['create', '-a', username, '-c', cap, start])
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(e)
        return {'success': False, 'error': "RPC exception"}
    return info

###############################################################################
# templates
###############################################################################

def render_accounts(error=None):
    accounts = getaccountinfo_rpc()
    return render_template("accounts.html", error=error, accounts=accounts)

def render_receipts(account):
    info = getaccountreceipts_rpc(account)
    if not info['success']:
        return render_accounts(error=info['error'])
    receipts = format_receipts(info['receipts'])
    return render_template('receipts.html', account_name=account,
                           receipts=receipts, n_receipts=len(receipts))

def render_login(error=None):
    return render_template('login.html', error=error,
                           account_cap=config['Account']['Cap'])

def render_register(error=None):
    return render_template('register.html', error=error)

###############################################################################
# app actions
###############################################################################

def format_receipts(receipts):
    # make info easy to render into templete
    new = []
    for session in receipts:
        new_session = {'entries': []}
        for entry in session:
            new_entry = {'values': []}
            for k, v in entry.items():
                if k == 'type':
                    t = v.replace("_", " ").title()
                    new_entry['type'] = t
                elif k == 'time':
                    t = time.ctime(v)
                    new_entry['time'] = t
                elif k == 'wad':
                    w = Wad.from_dict(v)
                    new_entry['values'].append(('Wad', str(w)))
                else:
                    new_entry['values'].append((k.title(), str(v)))
            new_session['entries'].append(new_entry)
        new.append(new_session)
    return new

def list_receipts(account):
    return render_receipts(account)

def generate_beacon(account):
    beacon = MoneysocketBeacon()
    location = WebsocketLocation(config['Beacon']['RelayLocation'])
    beacon.add_location(location)
    info = connect_rpc(account, str(beacon))
    if not info['success']:
        return render_accounts(error=info['error'])
    return render_accounts()

def clear_beacons(account):
    info = clear_rpc(account)
    if not info['success']:
        return render_accounts(error=info['error'])
    return render_accounts()

def remove_account(account):
    info = clear_rpc(account)
    if not info['success']:
        return render_accounts(error=info['error'])
    info = rm_rpc(account)
    if not info['success']:
        return render_accounts(error=info['error'])
    aa = AccountAssignment.query.filter_by(account_name=account).first()
    db.session.delete(aa)
    db.session.commit()
    return render_accounts()

def new_account(username):
    ua = user_accounts()
    limit = int(config['Account']['AccountsPerUser'])
    if len(ua) >= limit:
        return render_accounts(error="max %d accounts per user" % limit)
    info = create_rpc(username)
    if not info['success']:
        return render_accounts(error=info['error'])
    account_name = info['name']
    u = UserModel.query.filter_by(username=username).first()
    aa = AccountAssignment(user_id=u.id, account_name=account_name)
    db.session.add(aa)
    db.session.commit()
    return render_accounts()

###############################################################################
# redirect
###############################################################################

def redirect_path(path):
    return redirect(config['Server']['ExternalBaseUrl'] + path)

###############################################################################
# app interaction
###############################################################################

@app.route('/accounts', methods = ['POST', 'GET'])
def accounts():
    if not current_user.is_authenticated:
        logging.info("not logged in, redirecting")
        return redirect_path('/login')
    logging.info("accounts: %s" % request.method)
    if request.method == 'POST':
        if 'list_receipts' in request.form:
            action = 'list_receipts'
            logging.info(action)
            return list_receipts(request.form[action])
        elif 'generate_beacon' in request.form:
            action = 'generate_beacon'
            logging.info(action)
            return generate_beacon(request.form[action])
        elif 'clear_beacons' in request.form:
            action = 'clear_beacons'
            logging.info(action)
            return clear_beacons(request.form[action])
        elif 'remove_account' in request.form:
            action = 'remove_account'
            logging.info(action)
            return remove_account(request.form[action])
        elif 'new_account' in request.form:
            # TODO cap max accounts per user
            action = 'new_account'
            logging.info(action)
            return new_account(request.form[action])
        else:
            logging.info("unknown")
            return render_accounts(error="unknown action")
    else:
        logging.info("render accounts")
        # TODO filter by db ownership
        return render_accounts()

@app.route('/')
def root():
    logging.info("root: %s" % request.method)
    if current_user.is_authenticated:
        logging.info("current_user: %s" % current_user)
        return redirect_path('/accounts')
    else:
        return redirect_path('/login')

###############################################################################
# login lifecycle
###############################################################################

@app.route('/login', methods = ['POST', 'GET'])
def login():
    logging.info("login: %s" % request.method)
    if current_user.is_authenticated:
        logging.info("current_user: %s" % current_user)
        return redirect_path('/accounts')
    if request.method == 'POST':
        email = request.form['email']
        user = UserModel.query.filter_by(email = email).first()
        if user is not None and user.check_password(request.form['password']):
            login_user(user)
            return redirect_path('/accounts')
        return render_login(error="invalid username/pw")
    return render_login()


@app.route('/register', methods=['POST', 'GET'])
def register():
    logging.info("register: %s" % request.method)
    if current_user.is_authenticated:
        logging.info("current_user: %s" % current_user)
        return redirect_path('/accounts')
    if request.method == 'POST':
        if "email" not in request.form:
            return render_register(error='no email?')
        if "username" not in request.form:
            return render_register(error='no username?')
        if "password" not in request.form:
            return render_register(error='no password?')
        email = request.form['email']
        username = request.form['username']
        password = request.form['password']
        if len(password) < 8:
            return render_register(error='password must be 8 chars long')
        if UserModel.query.filter_by(email=email).first():
            return render_register(error='Email already present')
        if UserModel.query.filter_by(username=username).first():
            return render_register(error='Username already present')
        user = UserModel(email=email, username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return redirect_path('/login')
    return render_register()


@app.route('/logout')
def logout():
    logging.info("logout: %s" % request.method)
    logout_user()
    return redirect_path('/accounts')

###############################################################################
# load gunicorn stuff
###############################################################################


class CustodialApplication(BaseApplication):
    def __init__(self, options):
        self.options = options
        self.application = app
        super().__init__()

    def load_config(self):
        config = {key: value for key, value in self.options.items()
                  if key in self.cfg.settings and value is not None}
        for key, value in config.items():
            self.cfg.set(key.lower(), value)

    def load(self):
        return self.application

    def init(self, parser, opts, args):
        pass
