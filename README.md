This repository contains a script that attempts to convert package
repositories from a [Galaxy][galaxy] [Tool Shed][toolshed] (i.e. those
repositories defining software installations - not those defining the
tool XML Galaxy uses to create forms and command lines) into a
[Homebrew][homebrew] tap installable and usable via
[platform-brew][platform-brew].

The latest experimental tap created with this script can be found
[here][homebrewshed]. This scirpt has known limitations however and so
not all recipes are functional.

To test tool shed installs using brew - install brew, install platform-brew, and tap [homebrew-toolshed][homebrewshed] and use platform brew to test things out.

    % ruby -e "$(wget -O- https://raw.github.com/Homebrew/linuxbrew/go/install)" # if needed
    % brew tap jmchilton/platform
    % brew install --HEAD platform-brew
    % brew tap jmchilton/toolshed
    % brew vinstall jmchilton/toolshed/devteam_packageemboss500 1.0 --without-architecture
    % . <(brew env jmchilton/toolshed/devteam_packageemboss500 1.0)
    % etandem 
    Looks for tandem repeats in a nucleotide sequence

The ``shed2tap`` script to create the toolshed tap used in the above
example can be ran as follows:

    % virtualenv .venv; . .venv/bin/activate
    % pip install -r requirements.txt
    % python shed2tap.py --help
    Usage: shed2tap.py [OPTIONS]

    Options:
      --tool_shed [toolshed|testtoolshed]
                                  Tool shed to target.
      --owner TEXT                    Limit generation to specific owner.
      --name_filter TEXT              Apply regex to name filters.
      --git_user TEXT
      --brew_directory TEXT
      --help                          Show this message and exit.
      
    % python shed2tap.py --git_user jmchilton --tool_shed toolshed


[galaxy]: http://galaxyproject.org/
[toolshed]: https://toolshed.g2.bx.psu.edu/
[homebrew]: http://brew.sh/
[platform-brew]: https://github.com/jmchilton/platform-brew
[homebrewshed]: https://github.com/jmchilton/homebrew-toolshed
