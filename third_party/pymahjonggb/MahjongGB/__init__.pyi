"""
PyMahjongGB: A library for Fan Calculation of Mahjong in Chinese Standard Rule.

Author:
    Yunlong Lu (luyunlong@pku.edu.cn)

License:
    MIT License
"""

from typing import Tuple, Union


def MahjongFanCalculator(
    pack: Tuple[Tuple[str, str, int], ...],
    hand: Tuple[str, ...],
    winTile: str,
    flowerCount: int,
    isSelfDrawn: bool,
    is4thTile: bool,
    isAboutKong: bool,
    isWallLast: bool,
    seatWind: int,
    prevalentWind: int,
    verbose: bool = False
) -> Union[Tuple[Tuple[int, str], ...], Tuple[Tuple[int, int, str, str], ...]]:
    """
    Calculate Mahjong Fans.
    Parameters:
        pack - A tuple of fixed packs, each of which is a tuple of form ("CHI"/"PENG"/"GANG", tile, offer:0..3);
        hand - A tuple of standing tiles;
        winTile - Winning Tile;
        flowerCount - Number of flower tiles;
        isSelfDrawn - bool indicate self drawn;
        is4thTile - bool indicate 4th tile;
        isAboutKong - bool indicate about kong;
        isWallLast - bool indicate wall last;
        seatWind - seat wind of 0..3 indicate east/south/west/north;
        prevalentWind - prevalent wind of 0..3 indicate east/south/west/north.
        verbose - (Optional) bool control return format, default to be False.
    Returns:
        If verbose is False: 
            A tuple of fans, each of which is a tuple of form (fan_count, fan_name).
        If verbose is True:
            A tuple of fans, each of which is a tuple of form (fan_point, cnt, fan_name, fan_name_en).
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...


def MahjongShanten(
    pack: Tuple[Tuple[str, str, int], ...],
    hand: Tuple[str, ...]
) -> int:
    """
    Calculate Mahjong Shanten.
    Parameters:
        pack - A tuple of fixed packs, each of which is a tuple of form ("CHI"/"PENG"/"GANG", tile, offer:0..3);
        hand - A tuple of standing tiles;
    Returns:
        An integer of shanten.
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...


def ThirteenOrphansShanten(
    hand: Tuple[str, ...]
) -> Tuple[int, Tuple[str, ...]]:
    """
    Calculate Mahjong Shanten For Thirteen Orphans.
    Parameters:
        hand - A tuple of standing tiles;
    Returns:
        A tuple of (shanten, useful), where shanten is an integer, useful is a tuple of useful tiles.
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...


def SevenPairsShanten(
    hand: Tuple[str, ...]
) -> Tuple[int, Tuple[str, ...]]:
    """
    Calculate Mahjong Shanten For Seven Pairs.
    Parameters:
        hand - A tuple of standing tiles;
    Returns:
        A tuple of (shanten, useful), where shanten is an integer, useful is a tuple of useful tiles.
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...


def HonorsAndKnittedTilesShanten(
    hand: Tuple[str, ...]
) -> Tuple[int, Tuple[str, ...]]:
    """
    Calculate Mahjong Shanten For Honors And Knitted Tiles.
    Parameters:
        hand - A tuple of standing tiles;
    Returns:
        A tuple of (shanten, useful), where shanten is an integer, useful is a tuple of useful tiles.
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...


def KnittedStraightShanten(
    hand: Tuple[str, ...]
) -> Tuple[int, Tuple[str, ...]]:
    """
    Calculate Mahjong Shanten For Knitted Straight.
    Parameters:
        hand - A tuple of standing tiles;
    Returns:
        A tuple of (shanten, useful), where shanten is an integer, useful is a tuple of useful tiles.
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...


def RegularShanten(
    hand: Tuple[str, ...]
) -> Tuple[int, Tuple[str, ...]]:
    """
    Calculate Mahjong Shanten For Regular Form.
    Parameters:
        hand - A tuple of standing tiles;
    Returns:
        A tuple of (shanten, useful), where shanten is an integer, useful is a tuple of useful tiles.
    Raises:
        TypeError - If any invalid input is encountered.
    """
    ...
