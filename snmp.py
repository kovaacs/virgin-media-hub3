#!/usr/bin/python3
"""Basic SNMP support for the Virgin Media Hub

This module implements the underlying convenience classes for setting
and retrieving SNMP OIDs in a pythonic way.

"""
import datetime
import enum
import re
import textwrap
import warnings

import utils

@enum.unique
class IPVersion(enum.Enum):
    "IP Address Version"
    IPv4 = "1"
    IPv6 = "2"
    GodKnows = "4"

@enum.unique
class DataType(enum.Enum):
    """SNMP Data Types.

    ...I think...
    """
    INT = 2
    PORT = 66
    STRING = 4

@enum.unique
class Boolean(enum.Enum):
    """The hub's representation of True and False"""
    # Fixme: This is complete and utter guesswork
    TRUE = "1"
    FALSE = "0"

@enum.unique
class IPProtocol(enum.Enum):
    """IP IPProtocols"""
    UDP = "0"
    TCP = "1"
    BOTH = "2"

class RawAttribute:
    """An abstraction of an SNMP attribute.

    This behaves like a normal attribute: Reads of it will retrieve
    the value from the hub, and writes to it will send the value back
    to the hub.

    For convenience, the value will be cached so repeated reads can be
    done without needing multiple round-trips to the hub.

    This allows you to read/write the 'raw' values. For most use cases
    you probably want to use the Attribute class, as this can do
    translation.
    """
    def __init__(self, oid, datatype, value=None):
        self._oid = oid
        self._datatype = datatype
        self._value = value
        self._value_gotten = (value is not None)
        self.__doc__ = "SNMP Attribute {0}, assumed to be {1}".format(oid, datatype.name)

    @property
    def oid(self):
        """The SNMP Object Identifier"""
        return self._oid

    @property
    def datatype(self):
        """The Data Type - one of the DataType enums"""
        return self._datatype

    def refresh(self, instance):
        """Re-read the value from the hub"""
        self._value = instance.snmp_get(self._oid)
        self._value_gotten = True

    def __get__(self, instance, owner):
        if not self._value_gotten:
            self.refresh(instance)
        return self._value

    def __set__(self, instance, value):
        instance.snmp_set(self._oid, value, self._datatype)
        readback = instance.snmp_get(self._oid)
        if readback != value:
            raise ValueError("{hub} did not accept a value of '{value}' for {oid}: "
                             "It read back as '{rb}'!?"
                             .format(hub=instance,
                                     value=value,
                                     oid=self._oid,
                                     rb=readback))
        self._value = readback

    def __delete__(self, instance):
        raise NotImplementedError("Deleting SNMP values do not make sense")

class Translator:
    snmp_datatype = DataType.STRING
    @staticmethod
    def snmp(python_value):
        "Returns the input value"
        return python_value
    @staticmethod
    def pyvalue(snmp_value):
        "Returns the input value"
        return snmp_value

class NullTranslator(Translator):
    """A translator which does nothing.

    Except that it maps the empty string to None and back...
    """
    @staticmethod
    def snmp(python_value):
        if python_value is None:
            return ""
        return str(python_value)
    @staticmethod
    def pyvalue(snmp_value):
        if snmp_value == "":
            return None
        return snmp_value

class EnumTranslator(Translator):
    """A translator which translates based on Enums"""
    def __init__(self, enumclass, snmp_datatype=DataType.STRING):
        self.enumclass = enumclass
        self.snmp_datatype = snmp_datatype

    def snmp(self, python_value):
        return self.enumclass[python_value]
    def pyvalue(self, snmp_value):
        return self.enumclass(snmp_value)
    @property
    def name(self):
        self.__str__()
    def __str__(self):
        return "{0}({1})".format(self.__class__.__name__, self.enumclass.__name__)
    __repr__ = __str__

class BoolTranslator(Translator):
    "Translates python boolean values to/from the router's representation"
    snmp_datatype = DataType.INT
    @staticmethod
    def snmp(python_value):
        if isinstance(python_value, str) and python_value.lower() == "false":
            return "2"
        return "1" if python_value else "2"
    @staticmethod
    def pyvalue(snmp_value):
        return snmp_value == "1"

# pylint: disable=invalid-name
IPVersionTranslator = EnumTranslator(IPVersion)

class IntTranslator(Translator):
    """Translates integers values to/from the router's representation.

    Generally, the router represents them as decimal strings, but it
    is nice to have them typecast correctly.

    """
    snmp_datatype = DataType.INT
    @staticmethod
    def snmp(python_value):
        if python_value is None:
            return ""
        return str(int(python_value))
    @staticmethod
    def pyvalue(snmp_value):
        if snmp_value == "":
            return None
        return int(snmp_value)

class MacAddressTranslator(Translator):
    """
    The hub represents mac addresses as e.g. "$787b8a6413f5" - i.e. a
    dollar sign followed by 12 hex digits, which we need to transform
    to the traditional mac address representation.
    """
    @staticmethod
    def pyvalue(snmp_value):
        res = snmp_value[1:3]
        for idx in range(3, 13, 2):
            res += ':' + snmp_value[idx:idx+2]
        return res
    @staticmethod
    def snmp(python_value):
        raise NotImplementedError()

_IPV4_SNMP_RE = re.compile(r"\$[0-9a-fA-F]{8}")
_IPV4_PY_RE = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}")

class IPv4Translator(Translator):
    """Handles translation of IPv4 addresses to/from the hub.

    The hub encodes IPv4 addresses in hex, prefixed by a dollar sign,
    e.g. "$c2a80464" => 192.168.4.100
    """
    @staticmethod
    def snmp(python_value):
        "Translates an ipv4 address to something the hub understands"
        if python_value is None:
            return "$00000000"
        if not _IPV4_PY_RE.fullmatch(python_value):
            raise ValueError("PY Value '%s' does not look like a proper IPv4 Address"
                             % python_value)
        def tohex(decimal):
            return "{0:0>2s}".format(hex(int(decimal))[2:].lower())
        return "$" + ''.join(map(tohex, python_value.split('.')))

    @staticmethod
    def pyvalue(snmp_value):
        "Translates a hub-representation of an ipv4 address to a python-friendly form"
        if snmp_value in ["", "$00000000"]:
            return None
        if not _IPV4_SNMP_RE.fullmatch(snmp_value):
            raise ValueError("SNMP Value '%s' does not like a proper IPv4 address" % snmp_value)
        ipaddr = (str(int(snmp_value[1:3], base=16))
                  + '.' + str(int(snmp_value[3:5], base=16))
                  + '.' + str(int(snmp_value[5:7], base=16))
                  + '.' + str(int(snmp_value[7:9], base=16)))
        return ipaddr

_IPV6_SNMP_RE = re.compile(r"\$[0-9a-fA-F]{32}")

class IPv6Translator(Translator):
    """
        The router encodes IPv6 address in hex, prefixed by a dollar sign
    """

    @staticmethod
    def snmp(python_value):
        raise NotImplementedError()

    @staticmethod
    def pyvalue(snmp_value):
        if snmp_value in ["", "$00000000000000000000000000000000"]:
            return None
        if not _IPV6_SNMP_RE.fullmatch(snmp_value):
            raise ValueError("SNMP Value '%s' does not look like a proper IPv6 address"
                             % snmp_value)
        res = snmp_value[1:5]
        for chunk in range(5, 30, 4):
            res += ':' + snmp_value[chunk:chunk+4]
        return res

class IPAddressTranslator(Translator):
    """Translates to/from IP address. It will understand both IPv4 and IPv6 addresses"""
    @staticmethod
    def snmp(python_value):
        try:
            return IPv4Translator.snmp(python_value)
        except ValueError:
#            warnings.warn("python value '%s' was not an IPv4 address" % python_value)
            return IPv6Translator.snmp(python_value)
    @staticmethod
    def pyvalue(snmp_value):
        try:
            return IPv4Translator.pyvalue(snmp_value)
        except ValueError:
#            warnings.warn("SNMP value '%s' was not an IPv4 address" % snmp_value)
            return IPv6Translator.pyvalue(snmp_value)

class DateTimeTranslator(Translator):
    """
    Dates (such as the DHCP lease expiry time) are encoded somewhat stranger
    than even IP addresses:

    E.g. "$07e2030e10071100" is:
         0x07e2 : year = 2018
             0x03 : month = March
               0x0e : day-of-month = 14
                 0x10 : hour = 16 (seems to at least use 24hr clock!)
                   0x07 : minute = 07
                     0x11 : second = 17
                       0x00 : junk
    """
    @staticmethod
    def pyvalue(snmp_value):
        if snmp_value is None or snmp_value in ["", "$0000000000000000"]:
            return None
        year = int(snmp_value[1:5], base=16)
        month = int(snmp_value[5:7], base=16)
        dom = int(snmp_value[7:9], base=16)
        hour = int(snmp_value[9:11], base=16)
        minute = int(snmp_value[11:13], base=16)
        second = int(snmp_value[13:15], base=16)
        return datetime.datetime(year, month, dom, hour, minute, second)

    @staticmethod
    def snmp(python_value):
        raise NotImplementedError()

class Attribute(RawAttribute):
    """A generic SNMP Attribute which can use a translator.

    This allows us to have pythonic variables representing OID values:
    Reads will retrieve the value from router, and writes will update
    the route - with the translator doing the necessary translation
    between Python values and router representation.

    """
    def __init__(self, oid, translator=NullTranslator, value=None, doc=None):
        RawAttribute.__init__(self, oid, datatype=translator.snmp_datatype, value=value)
        self._translator = translator
        try:
            translator_name = translator.__name__
        except AttributeError:
            translator_name = translator.name

        if doc:
            self.__doc__ = textwrap.dedent(doc) + \
                "\n\nCorresponds to SNMP attribute {0}, translated by {1}" \
                .format(oid, translator_name)
        else:
            self.__doc__ = "SNMP Attribute {0}, as translated by {1}" \
                .format(oid, translator_name)

    def __get__(self, instance, owner):
        return self._translator.pyvalue(RawAttribute.__get__(self, instance, owner))

    def __set__(self, instance, value):
        return RawAttribute.__set__(self, instance, self._translator.snmp(value))

class TransportProxy:
    """Forwards snmp_get/snmp_set calls to another class/instance."""
    def __init__(self, transport):
        """Create a TransportProxy which forwards to the given transport"""
        self._transport = transport
    def snmp_get(self, *args, **kwargs):
        return self._transport.snmp_get(*args, *kwargs)
    def snmp_set(self, *args, **kwargs):
        return self._transport.snmp_set(*args, *kwargs)
    def snmp_walk(self, *args, **kwargs):
        return self._transport.snmp_walk(*args, *kwargs)

class TransportProxyDict(TransportProxy, dict):
    def __init__(self, transport, cells=None):
        TransportProxy.__init__(self, transport)

class RowBase(TransportProxy):
    def __init__(self, proxy, keys):
        super().__init__(proxy)
        self._keys = keys

    def keys(self):
        return self._keys

    def values(self):
        return [getattr(self, name) for name in self._keys]

    def __len__(self):
        return len(self._keys)

    def __getitem__(self, key):
        return getattr(self, self._keys[key])

    def __iter__(self):
        return self.keys().iter()

    def __contains__(self, item):
        return item in self._keys

    def __str__(self):
        return self.__class__.__name__ + '(' \
            + ', '.join([key+'="'+str(getattr(self, key))+'"'
                         for key in self._keys]) \
            + ')'

    def __repr__(self):
        return self.__class__.__name__ + '(' \
            + ', '.join([key+'="'+repr(getattr(self, key))+'"'
                         for key in self._keys]) \
            + ')'


def parse_table(table_oid, walk_result):
    """Restructure the result of an SNMP table into rows and columns

    """
    def column_id(oid):
        return oid[len(table_oid)+1:].split('.')[0]

    def row_id(oid):
        return '.'.join(oid[len(table_oid)+1:].split('.')[1:])

    result_dict = dict()
    for oid, raw_value in walk_result.items():
        this_column_id = column_id(oid)
        this_row_id = row_id(oid)
        if this_row_id not in result_dict:
            result_dict[this_row_id] = dict()
        result_dict[this_row_id][this_column_id] = raw_value
    return result_dict

class Table(TransportProxyDict):
    """A pythonic representation of an SNMP table

    The python representation of the table is a dict() - not an array,
    as each entry in the table has an ID: the ID becomes the key of
    the resulting dict.

    Each entry in the result is a (customised) RowBase class, where
    SNMP attributes are mapped to Attribute instances: Updates to the
    attributes will result in the hub being updated.

    Although the resulting table is updateable (updates to attributes
    in the row will result in SNMP Set calls), the table does not
    support deletion or insertion of elements: it is of fixed size.

    The column_mapping describes how to translate OID columns to
    Python values in the resulting rows:

    {
      "1": {"name": "port_number",
            "translator": snmp.IntTranslator,
            "doc": "Port number for Foobar"},
      "2": {"name": "address",
            "translator": snmp.IPv4Translator}
    }

    The keys in the dict correspond to the SNMP OID column numbers -
    i.e. the first part after the table_oid.

    The values of each key must be a dict, where the following keys
    are understood:

    - "name": (mandatory) The resulting python attribute name. This must be a
              valid python attribute name.

    - "translator": (optional) The class/instance of a translator to
                    map between python and SNMP representations. If
                    none is given, the default NullTranslator will be
                    used.

    - "doc": (optional) the doc string to associate with the attribute.
    """
    def __init__(self, transport, table_oid, column_mapping, walk_result=None):
        """Instantiate a new table based on an SNMP walk

        """
        super().__init__(transport)

        if not walk_result:
            walk_result = transport.snmp_walk(table_oid)

        if not walk_result:
            warnings.warn("SNMP Walk of '%s' yielded no results" % table_oid)

        rawtable = parse_table(table_oid, walk_result)

        result_dict = dict()
        for row_id, row in rawtable.items():
            result_dict[row_id] = dict()
            for column_id, raw_value in row.items():
                if not column_id in column_mapping:
                    continue
                result_dict[row_id][column_id] = (table_oid + '.' + column_id + '.' + row_id,
                                                  raw_value,
                                                  column_mapping[column_id])
            if not result_dict[row_id]:
                del result_dict[row_id]

        # Then go through the result, and create a row object for each
        # row. Essentially, each row is a different class, as it may
        # have different attributes
        for rowkey, row in result_dict.items():
            # Build up the columns in the row
            class_dict = {mapping["name"]: Attribute(oid=oid,
                                                     value=raw_value,
                                                     doc=mapping.get('doc'),
                                                     translator=mapping.get('translator',
                                                                            NullTranslator))
                          for oid, raw_value, mapping in row.values()}
            if not class_dict:
                # Empty rows are not interesting...
                continue
            # A litle trick: Redo it with a new dict, so we can get
            # the order "right" - i.e. the order it is done in the
            # mappings
            class_dict = {column['name']: class_dict[column['name']]
                          for column in column_mapping.values()
                          if column['name'] in class_dict}

            RowClass = type('Row', (RowBase,), class_dict)
            self[rowkey] = RowClass(self, class_dict)

        if len(self) == 0:
            warnings.warn("SMTP walk of %s resulted in zero rows"
                          % table_oid)

    def format(self):
        """Get a string representation of the table for human consumption.

        This is nicely ordered in auto-sized columns with headers and
        (almost) graphics - e.g:

            +-------------+--------+---------------+-----------------------------------------+
            | IPAddr      | Prefix | NetMask       | GW                                      |
            +-------------+--------+---------------+-----------------------------------------+
            | 86.21.83.42 | 21     | 255.255.248.0 | 86.21.80.1                              |
            |             | 0      |               | 0000:000c:000f:cea0:000f:caf0:0000:0000 |
            +-------------+--------+---------------+-----------------------------------------+
        """
        return utils.format_table(self.aslist())

    def aslist(self):
        """Get the rows as a list

        This will 'lose' the ID of the rows, which most of the time is
        not a problem.

        """
        return self.values()
