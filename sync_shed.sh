#!/bin/bash
set -e

DATE=`date`

SHED2TAP_DIRECTORY=${SHED2TAP_DIRECTORY:-`dirname $0`}
SHED2TAP_VENV=${SHED2TAP_VENV:-"${SHED2TAP_DIRECTORY}/.venv"}
if [ ! -e "$SHED2TAP_VENV" ];
then
    virtualenv "$SHED2TAP_VENV"
fi

SHED2TAP_FILERS=${SHED2TAP_FILTERS:-""}

. "$SHED2TAP_VENV/bin/activate"
echo "${SHED2TAP_DIRECTORY}/requirements.txt"
pip install -r "${SHED2TAP_DIRECTORY}/requirements.txt"

GIT_USER="$1"
TOOLSHED="$2"

GIT_REPOSITORY_NAME="homebrew-${TOOLSHED}"
GIT_REPOSITORY="git@github.com:${GIT_USER}/${GIT_REPOSITORY_NAME}.git"
GIT_TARGET="${TOOLSHED}"

# TODO: Modify following line for that rotten apple OS. 
BREW_DIRECTORY="${HOME}/.linuxbrew"
BREW_TAP_DIRECTORY="${BREW_DIRECTORY}/Library/Taps/${GIT_USER}/${GIT_REPOSITORY_NAME}"

if [ ! -e "${GIT_TARGET}" ];
then
    git clone "${GIT_REPOSITORY}" "${GIT_TARGET}"
else
    # TODO: update...
    git --git-dir="${GIT_TARGET}/.git" fetch origin
    git --git-dir="${GIT_TARGET}/.git" checkout origin/master
    git --git-dir="${GIT_TARGET}/.git" branch -D master
    git --git-dir="${GIT_TARGET}/.git" checkout -b master
fi
python "${SHED2TAP_DIRECTORY}/shed2tap.py" --git_user="${GIT_USER}" --tool_shed="${TOOLSHED}" ${SHED2TAP_FILTERS}

if [ ! -n "${SHED2TAP_FILTERS}" ];
then
    echo "Clearing out git repository working directory."
    rm "${GIT_TARGET}"/*rb
fi

cp "${BREW_TAP_DIRECTORY}"/* "${GIT_TARGET}"

cd "${GIT_TARGET}"

# If doing a full sync remove files no longer produced by shed2tap.
if [ ! -n "${SHED2TAP_FILTERS}" ];
then
    git  --git-dir="${GIT_TARGET}/.git" rm $(git  --git-dir="${GIT_TARGET}/.git" ls-files --deleted) || echo "No files deleted."
fi

git add .
git commit -m "Automated synchronization with ${TOOLSHED} at ${DATE}." || echo "No changes."
git push origin master
