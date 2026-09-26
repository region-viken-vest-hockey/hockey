"""Minimal codec for the ``lz-string`` compression format.

Ports ``decompressFromUTF16``/``compressToUTF16`` (and their shared
``_decompress``/``_compress`` cores) from the reference ``lz-string``
JavaScript implementation (pieroxy/lz-string, MIT licensed). Production code
only needs the decoder -- StyledCalendar's public events API compresses its
JSON payload with ``LZString.compressToUTF16`` before returning it -- but the
encoder is kept alongside it so the codec can be roundtrip-tested without a
live fixture captured from that API.
"""

from __future__ import annotations


def _decompress(length: int, reset_value: int, get_next_value) -> str | None:
    dictionary: dict[int, str] = {0: chr(0), 1: chr(1), 2: chr(2)}
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    result: list[str] = []

    state = {
        "val": get_next_value(0),
        "position": reset_value,
        "index": 1,
    }

    def read_bits(n: int) -> int:
        bits = 0
        power = 1
        maxpower = 1 << n
        while power != maxpower:
            resb = state["val"] & state["position"]
            state["position"] >>= 1
            if state["position"] == 0:
                state["position"] = reset_value
                state["val"] = get_next_value(state["index"])
                state["index"] += 1
            bits |= (1 if resb > 0 else 0) * power
            power <<= 1
        return bits

    next_token = read_bits(2)
    if next_token == 0:
        c = chr(read_bits(8))
    elif next_token == 1:
        c = chr(read_bits(16))
    else:
        return ""

    dictionary[3] = c
    w = c
    result.append(c)

    while True:
        if state["index"] > length:
            return ""

        c_code = read_bits(num_bits)

        if c_code in (0, 1):
            entry_bits = 8 if c_code == 0 else 16
            dictionary[dict_size] = chr(read_bits(entry_bits))
            c_code = dict_size
            dict_size += 1
            enlarge_in -= 1
        elif c_code == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

        if c_code in dictionary:
            entry = dictionary[c_code]
        elif c_code == dict_size:
            entry = w + w[0]
        else:
            return None

        result.append(entry)

        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1


def decompress_from_utf16(compressed: str | None) -> str | None:
    """Decode a string produced by ``LZString.compressToUTF16``."""
    if compressed is None:
        return ""
    if compressed == "":
        return None
    return _decompress(len(compressed), 16384, lambda index: ord(compressed[index]) - 32)


def _compress(uncompressed: str, bits_per_char: int, get_char_from_int) -> str:
    if uncompressed is None:
        return ""

    dictionary: dict[str, int] = {}
    dictionary_to_create: set[str] = set()
    w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2
    data: list[str] = []
    data_val = 0
    data_position = 0

    def emit_bit(bit: int) -> None:
        nonlocal data_val, data_position
        data_val = (data_val << 1) | bit
        if data_position == bits_per_char - 1:
            data_position = 0
            data.append(get_char_from_int(data_val))
            data_val = 0
        else:
            data_position += 1

    def emit_value(value: int, bit_count: int) -> None:
        for _ in range(bit_count):
            emit_bit(value & 1)
            value >>= 1

    def emit_word(word: str) -> None:
        nonlocal enlarge_in, num_bits
        if word in dictionary_to_create:
            code_point = ord(word[0])
            if code_point < 256:
                emit_value(0, num_bits)
                emit_value(code_point, 8)
            else:
                emit_value(1, num_bits)
                emit_value(code_point, 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 1 << num_bits
                num_bits += 1
            dictionary_to_create.discard(word)
        else:
            emit_value(dictionary[word], num_bits)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

    for c in uncompressed:
        if c not in dictionary:
            dictionary[c] = dict_size
            dict_size += 1
            dictionary_to_create.add(c)

        wc = w + c
        if wc in dictionary:
            w = wc
            continue

        emit_word(w)
        dictionary[wc] = dict_size
        dict_size += 1
        w = c

    if w != "":
        emit_word(w)

    # Mark the end of the stream.
    emit_value(2, num_bits)

    # Flush the last (partial) char.
    while True:
        data_val <<= 1
        if data_position == bits_per_char - 1:
            data.append(get_char_from_int(data_val))
            break
        data_position += 1

    return "".join(data)


def compress_to_utf16(uncompressed: str | None) -> str:
    """Encode a string the way ``LZString.compressToUTF16`` would.

    Exists to make the decoder above roundtrip-testable without depending on
    a live-captured fixture.
    """
    if uncompressed is None:
        return ""
    return _compress(uncompressed, 15, lambda value: chr(value + 32)) + " "
