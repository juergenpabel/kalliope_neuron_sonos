#!/usr/bin/env python3

import sys
import logging
#import soco
from ipaddress import *
from difflib import SequenceMatcher
from requests.exceptions import ConnectTimeout
from urllib3.exceptions import TimeoutError
from ast import literal_eval

from soco import SoCo
from soco.groups import ZoneGroup
from soco.discovery import by_name as SoCo_ByName
from soco.music_library import MusicLibrary
from soco.exceptions import SoCoException
from soco.data_structures import *
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

        klass = self.__class__
        if not hasattr(klass, 'config'):
            klass.config = dict()
        if not hasattr(klass, 'sonos'):
            klass.sonos = dict()
        if not hasattr(klass, 'soco'):
            klass.soco = None

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
                raise InvalidParameterException("You must specify a valid sonos zone name for 'room' in init action")
            if ipv4 is not None:
                try:
                    if IPv4Address(ipv4).is_global:
                        raise InvalidParameterException("You must specify a private range IP address for 'ipv4' in init action")
                except ValueError:
                    raise InvalidParameterException("The configured value for 'ipv4'(='%s') is not a valid IPv4 address" % kwargs.get('ipv4',"") )
        else:
            klass = self.__class__
            if klass.soco is None:
                Utils.print_warning("[sonos] uninitialized neuron (probably failure during action='init' => SONOS unavailable?), not executing action='%s'" % self.action)
                return False
        return True


    def do_init(self, **kwargs):
        klass = self.__class__
        ipv4 = kwargs.get('ipv4', None)
        room = kwargs.get('room', None)
        logger.debug("[sonos] instantiating SoCo (ipv4='%s',room='%s')" % (ipv4, room))
        if ipv4 is None:
            logger.debug("[sonos] instantiating SoCo using discovery")
            klass.soco = SoCo_ByName(room)
        else:
            logger.debug("[sonos] instantiating SoCo using IP address")
            try:
                klass.soco = SoCo(ipv4)
                if klass.soco.is_visible is False:
                    raise SonosException("SONOS with ipv4='%s' is no coordinator, maybe slave in stereo pair? (you should probably use ipv4='%s')" % (ipv4, klass.soco.group.coordinator.ip_address))
            except (ConnectTimeout, TimeoutError, SoCoException) as e:
                raise SonosException("Failure while trying to communicate with SONOS (%s)" % e.__class__.__name__)
        if klass.soco is None:
            raise SonosException("Could not initialize sonos neuron with ipv4='%s' and room='%s', " % (ipv4, room))

        klass.config['room'] = room
        klass.config['rooms'] = dict()
        rooms = kwargs.get('rooms', dict())
        if isinstance(rooms, str):
            rooms = literal_eval(rooms)
        for name, members in rooms.items():
            if isinstance(members, list):
                klass.config['rooms'][name] = members
            elif isinstance(members, str):
                klass.config['rooms'][name] = [members]
            else:
                Utils.print_warning("[sonos] expected config setting 'rooms' to be a list (of rooms) or string (single room), got instance of '%s' for room '%s'" % ( type(members), name))
        self.do_sync()
        if klass.config['room'] not in klass.sonos['rooms']:
            raise InvalidParameterException("You must specify a valid sonos name for 'room' in action '%s'" % self.action)


    def do_play(self, **kwargs):
        klass = self.__class__
        item = kwargs.get('item', None)
        room = kwargs.get('room', klass.config['room'])
        if room not in klass.sonos['rooms']:
            raise InvalidParameterException("The value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))

        logger.debug("[sonos] configuring room '%s' in SONOS" % (room))
        soco = klass.sonos['rooms'][room][0] # first entry in list is assumed target
        logger.debug("[sonos] using SoCo(%s) as coordinator for room '%s'" % (soco.ip_address, room))
        soco.unjoin()
        for player in klass.sonos['rooms'][room][1:]:
            try:
                if player.group.coordinator != player:
                    logger.debug("[sonos] unjoining SoCo(%s) from '%s'" % (player.ip_address, player.group.label))
                    player.unjoin()
                logger.debug("[sonos] joining SoCo(%s) to room '%s'" % (player.ip_address, room))
                player.join(soco)
            except (ConnectTimeout, TimeoutError, SoCoException) as e:
                Utils.print_warning("Failure while trying to communicate with SONOS (%s)" % e.__class__.__name__)
        if item is not None:
            results = dict()
            for favorite in klass.sonos['favorites']:
                ratio = SequenceMatcher(None, item, favorite).ratio()
                results[ratio] = favorite
            result = sorted(results.keys(), reverse=True)[0] # get highest score
            result = results[result] # get best match
            logger.debug("[sonos] playing '%s' from favorites in room '%s'" % (result, room))
            soco.clear_queue()
            soco.add_to_queue(klass.sonos['favorites'][result])
            soco.play_from_queue(0)
        else:
            logger.debug("[sonos] playing queue in room '%s'" % (result, room))
            soco.play()


    def do_pause(self, **kwargs):
        klass = self.__class__
        room = kwargs.get('room', klass.config['room'])
        if room not in klass.sonos['rooms']:
            raise InvalidParameterException("The value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = klass.sonos['rooms'][room][0]
        soco.pause()


    def do_next(self, **kwargs):
        klass = self.__class__
        room = kwargs.get('room', klass.config['room'])
        if room not in klass.sonos['rooms']:
            raise InvalidParameterException("The value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = klass.sonos['rooms'][room][0]
        soco.next()


    def do_prev(self, **kwargs):
        klass = self.__class__
        room = kwargs.get('room', klass.config['room'])
        if room not in klass.sonos['rooms']:
            raise InvalidParameterException("The value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = klass.sonos['rooms'][room][0]
        soco.previous()


    def do_mute(self, **kwargs):
        klass = self.__class__
        room = kwargs.get('room', klass.config['room'])
        if room not in klass.sonos['rooms']:
            raise InvalidParameterException("The value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = klass.sonos['rooms'][room][0]
        soco.mute = True


    def do_unmute(self, **kwargs):
        klass = self.__class__
        room = kwargs.get('room', klass.config['room'])
        if room not in klass.sonos['rooms']:
            raise InvalidParameterException("The value '%s' is invalid for parameter 'room' in action '%s'" % (room, self.action))
        soco = klass.sonos['rooms'][room][0]
        soco.mute = False


    def do_sync(self, **kwargs):
        klass = self.__class__
        klass.sonos['zones'] = dict()
        klass.sonos['rooms'] = dict()
        try:
            for player in klass.soco.visible_zones:
                try:
                    if isinstance(player, ZoneGroup):
                        player = player.coordinator
                    logger.debug("[sonos] discovered zone '%s' with ipv4='%s'" % (player.player_name, player.ip_address))
                    klass.sonos['zones'][player.player_name] = player
                except (ConnectTimeout, TimeoutError) as e:
                    Utils.print_warning("[sonos] failed to add SoCo(%s) due to a timeout (player offline?)" % (player.ip_address))
        except (ConnectTimeout, TimeoutError, SoCoException) as e:
            Utils.print_warning("[sonos] error communicating with SONOS (offline?): %s" % (str(e)))

        for name, player in klass.sonos['zones'].items():
            logger.debug("[sonos] creating (discovered) room '%s'" % (name))
            klass.sonos['rooms'][name] = [player]
        try:
            for name in klass.config['rooms']:
                if name not in klass.sonos['rooms']:
                    logger.debug("[sonos] creating (user-defined) room '%s'" % (name))
                    klass.sonos['rooms'][name] = list()
                    for member in klass.config['rooms'][name]:
                        if member in klass.sonos['zones']:
                            klass.sonos['rooms'][name].append(klass.sonos['zones'][member])
                        else:
                            Utils.print_warning("[sonos] unknown member '%s' referenced in (user-defined) room '%s'" % (member.label, room))
                else:
                    Utils.print_warning("[sonos] (user-defined) room '%s' (from settings) already exists in SONOS, ignoring" % (room))
        except (BaseException, ConnectTimeout, TimeoutError, SoCoException) as e:
            Utils.print_warning("[sonos] error while merging (user-defined) rooms from settings: %s" % (str(e)))
        finally:
            SettingEditor.set_variables({'sonos_rooms': {k.lower(): k for k, v in klass.sonos['rooms'].items()}})

        klass.sonos['favorites'] = dict()
        try:
            for favorite in MusicLibrary(klass.soco).get_sonos_favorites():
                klass.sonos['favorites'][favorite.title] = favorite
        except (ConnectTimeout, TimeoutError, SoCoException) as e:
            Utils.print_warning("[sonos] error while retrieving favorites from sonos: %s" % (str(e)))
        finally:
            logger.debug("[sonos] adding favorites by title as kalliope global variables (sonos_favorites[title]): %s" % klass.sonos['favorites'].keys())
            SettingEditor.set_variables({'sonos_favorites': {k.lower(): k for k, v in klass.sonos['favorites'].items()}})
        Utils.print_success("[sonos] syncing with SONOS successful")

