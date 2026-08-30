from setuptools import setup, Extension

module = Extension('MahjongGB', sources=[
    'MahjongGB/mahjong.cpp',
    'MahjongGB/mahjong-algorithm/fan_calculator.cpp',
    'MahjongGB/mahjong-algorithm/shanten.cpp'
    ], language='c++', extra_compile_args = ["-std=c++11"])

setup(name = "PyMahjongGB",
    version = "1.4.0",
    author = "Yunlong Lu",
    author_email = "luyunlong@pku.edu.cn",
    description = "A library for Fan Calculation of Mahjong in Chinese Standard Rule.",
    long_description = ''.join(open("README.md", encoding = "utf-8").readlines()),
    long_description_content_type = "text/markdown",
    url = "https://github.com/ailab-pku/MahjongGB",
    ext_modules = [module],
    python_requires = ">=3.5",
    packages=["MahjongGB"],
)
