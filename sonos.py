#!/usr/bin/env python3

import sys
import logging
#import soco
from ipaddress import *
from difflib import SequenceMatcher
from requests.exceptions import ConnectTimeout
from urllib3.exceptions import TimeoutError

from soco import *
from soco.data_structures import *
from soco.music_library import MusicLibrary
from soco.discovery import by_name as SoCo_ByName
from soco.exceptions import SoCoException
from kalliope.core.NeuronModule import NeuronModule, MissingParameterException, InvalidParameterException
from kalliope.core.NeuronExceptions import NeuronExceptions
from kalliope.core.Utils.Utils import Utils
from kalliope.core.ConfigurationManager import SettingEditor

logging.basicConfig()
logger = logging.getLogger("kalliope")


class SonosException(NeuronExceptions):
    def __init__(self, message, **kwargs):
        NeuronExceptions.__init__(self, **kwargs)
        self.message = message


class Sonos(NeuronModule):

    def __init__(self, **kwargs):
        NeuronModule.__init__(self, **kwargs)
        self.action = kwargs.get('action', None)
        self.actions = {}
        self.actions["init"] = self.do_init
        self.actions["play"] = self.do_play
        self.actions["pause"] = self.do_pause
        self.actions["stop"] = self.do_pause
        self.actions["next"] = self.do_next
        self.actions["previous"] = self.do_prev
        self.actions["mute"] = self.do_mute
        self.actions["unmute"] = self.do_unmute
        self.actions["sync"] = self.do_sync

        self.klass = self.__class__
        if not hasattr(self.klass, 'soco'):
            self.klass.soco = None
        if not hasattr(self.klass, 'household'):
            self.klass.household = dict()

        # check if parameters have been provided
        if self._is_parameters_ok(**kwargs):
            if kwargs.get('room', None) == "":
                del kwargs['room']
            self.actions[self.action](**kwargs)
        else:
            raise SonosException("Could not execute action='%s', SONOS probably offline" % self.action)

    def _is_parameters_ok(self, **kwargs):
        """
        Check if received parameters are ok to perform operations in the neuron
        :return: true if parameters are ok, raise an exception otherwise

        .. raises:: MissingParameterException, InvalidParameterException
        """
        if self.action is None:
            raise MissingParameterException("You must specify a value for 'action'")
        if self.action not in self.actions:
            raise InvalidParameterException("The configured value for 'action'(='%s') is not a valid action" % self.action)
        ipv4 = kwargs.get('ipv4', None)
        room = kwargs.get('room', None)
        if self.action == "init":
            if room is None:
                raise InvalidParameterException("You must specify a valid sonos zone/group name for 'room' in init action")
            if ipv4 is not None:
                try:
                    if IPv4Address(ipv4).is_global:
                        raise InvalidParameterException("You must specify a private range IP address for 'ipv4' in init action")
                except ValueError:
                    raise InvalidParameterException("The configured value for 'ipv4'(='%s') is not a valid IPv4 address" % kwargs.get('ipv4',"") )
        else:
            if self.klass.soco is None:
                Utils.print_warning("[sonos] uninitialized neuron (probably failure during action='init' => SONOS unavailable?), not executing action='%s'" % self.action)
                return False
        return True


    def do_init(self, **kwargs):
        ipv4 = kwargs.get('ipv4', None)
        room = kwargs.get('room', None)
        self.klass.household['room'] = room
        logger.debug("[sonos] instantiating SoCo (ipv4='%s',room='%s')" % (ipv4, room))
        if ipv4 is None:
            logger.debug("[sonos] instantiating SoCo using discovery")
            self.klass.soco = SoCo_ByName(room)
        else:
            logger.debug("[sonos] instantiating SoCo using IP address")
            try:
                soco = SoCo(ipv4)
                if soco.is_coordinator is True:
                    self.klass.soco = soco
                else:
                    Utils.print_warning("[sonos] SONOS with ipv4='%s' is no coordinator, using group coordinator" % ipv4)
                    self.klass.soco = soco.group.coordinator
            except (ConnectTimeout, TimeoutError, SoCoException) as e:
                raise SonosException("Failure while trying to communicate with SONOS (%s)" % e.__class__.__name__)

        if self.klass.soco is None:
            raise SonosException("Could not initialize sonos neuron with ipv4='%s' and room='%s', " % (ipv4, room))

        self.klass.household['configured-rooms'] = dict()
        for name, members in kwargs.get('rooms', dict()).items():
            if isinstance(members, list):
                self.klass.household['configured-rooms'][name] = members
            elif isinstance(members, str):
                self.klass.household['configured-rooms'][name] = [members]
            else:
                Utils.print_warning("[sonos] expected config setting 'rooms' to be a list (of rooms) or string (single room), got instance of '%s' for room '%s'" % ( type(members), name))
        self.do_sync()
        if self.klass.household['rooms']:
            if room not in self.klass.household['rooms']:
                raise InvalidParameterException("You must specify a valid sonos name for 'room' in action '%s'" % self.action)
        else:
            raise SonosException("SONOS returned an empty room list (wtf?)")


    def do_play(self, **kwargs):
        item = kwargs.get('item', None)
        room = kwargs.get('room', self.klass.household['room'])
        if room not in self.klass.household['rooms']:
            raise InvalidParameterException("the value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))

        soco = self.klass.household['rooms'][room][0] # first entry in list is assumed target
        if item is not None:
            results = dict()
            for favorite in self.klass.household['favorites']:
                ratio = SequenceMatcher(None, item, favorite).ratio()
                results[ratio] = favorite
            result = sorted(results.keys(), reverse=True)[0]
            result = results[result]
            soco.clear_queue()
            soco.add_to_queue(self.klass.household['favorites'][result])
            soco.play_from_queue(0)
        else:
            soco.play()


    def do_pause(self, **kwargs):
        room = kwargs.get('room', self.klass.household['room'])
        if room not in self.klass.household['rooms']:
            raise InvalidParameterException("the value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = self.klass.household['rooms'][room][0]
        soco.pause()


    def do_next(self, **kwargs):
        room = kwargs.get('room', self.klass.household['room'])
        if room not in self.klass.household['rooms']:
            raise InvalidParameterException("the value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = self.klass.household['rooms'][room][0]
        soco.next()


    def do_prev(self, **kwargs):
        room = kwargs.get('room', self.klass.household['room'])
        if room not in self.klass.household['rooms']:
            raise InvalidParameterException("the value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = self.klass.household['rooms'][room][0]
        soco.previous()


    def do_mute(self, **kwargs):
        room = kwargs.get('room', self.klass.household['room'])
        if room not in self.klass.household['rooms']:
            raise InvalidParameterException("the value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = self.klass.household['rooms'][room][0]
        soco.mute = True


    def do_unmute(self, **kwargs):
        room = kwargs.get('room', self.klass.household['room'])
        if room not in self.klass.household['rooms']:
            raise InvalidParameterException("the value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = self.klass.household['rooms'][room][0]
        soco.mute = False


    def do_sync(self, **kwargs):
        self.klass.household['rooms'] = dict()
        try:
            for group in self.klass.soco.all_groups:
                try:
                    for member in group.members:
                        if member.is_visible:
                            logger.debug("[sonos] assigning SoCo(%s) to room '%s'" % (member.ip_address, member.player_name))
                            self.klass.household['rooms'][member.player_name] = [member]
                except (ConnectTimeout, TimeoutError, SoCoException) as e:
                    Utils.print_warning("exception, failed to add group '%s' to rooms" % (group.coordinator.ip_address))
        except (ConnectTimeout, TimeoutError, SoCoException) as e:
            Utils.print_warning("[sonos] error communicating with SONOS (offline?): %s" % (str(e)))

        if self.klass.household['rooms']:
            try:
                for room in self.klass.household['configured-rooms']:
                    logger.debug("[sonos] merging pre-configured room '%s'" % (room))
                    if room not in self.klass.household['rooms']:
                        self.klass.household['rooms'][room] = list()
                        for member in self.klass.household['configured-rooms'][room]:
                            if member in self.klass.household['rooms']:
                                soco = self.klass.household['rooms'][member][0] ## first entry is assumed coordinator
                                logger.debug("[sonos] assigning SoCo(%s) to room '%s'" % (soco.ip_address, room))
                                self.klass.household['rooms'][room].extend(self.klass.household['rooms'][member])
                            else:
                                Utils.print_warning("[sonos] non-existing room '%s' in rooms settings" % (member.label))
                    else:
                        Utils.print_warning("[sonos] room '%s' (from rooms settings) already defined in SONOS, ignoring" % (room))
            except (BaseException, ConnectTimeout, TimeoutError, SoCoException) as e:
                Utils.print_warning("[sonos] error while merging defined rooms from settings: %s" % (str(e)))
            finally:
                SettingEditor.set_variables({'sonos_rooms': {k.lower(): k for k, v in self.klass.household['rooms'].items()}})

        self.klass.household['favorites'] = dict()
        try:
            for favorite in MusicLibrary(self.klass.soco).get_sonos_favorites():
                self.klass.household['favorites'][favorite.title] = favorite.reference
        except (ConnectTimeout, TimeoutError, SoCoException) as e:
            Utils.print_warning("[sonos] error while retrieving favorites from sonos: %s" % (str(e)))
        finally:
            logger.debug("[sonos] adding favorites by title as kalliope global variables (sonos_favorites[title])")
            SettingEditor.set_variables({'sonos_favorites': {k.lower(): k for k, v in self.klass.household['favorites'].items()}})
        Utils.print_success("[sonos] syncing with SONOS successful")

