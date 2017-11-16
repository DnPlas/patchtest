SUMMARY = "An image containing the packages required by patchtest and patchtest-oe"
DESCRIPTION = "An image containing the packages that patchtest and patchtest-oe, used by the former as guest machine to test oe-core patches"
HOMEPAGE = "http://git.yoctoproject.org/cgit/cgit.cgi/patchtest/"

IMAGE_FSTYPES = "ext4"

# include 500MB of extra space so it can store oe-core & bitbake
IMAGE_ROOTFS_EXTRA_SPACE = "512000"

IMAGE_INSTALL = "\
    packagegroup-core-boot \
    packagegroup-self-hosted \
    packagegroup-patchtest-oe \
    "
inherit core-image

fakeroot do_populate_patchtest_src() {
    # set the correct LC_ALL, required for python3 based applications
    echo "export LC_ALL=en_US.utf8" >> ${IMAGE_ROOTFS}/home/patchtest/.bashrc
    echo "export LANG=en_US.utf8" >> ${IMAGE_ROOTFS}/home/patchtest/.bashrc

    # define patchtest runner, called from initscript
    cat >> ${IMAGE_ROOTFS}/home/patchtest/.bashrc << EOF
function pt() {
    local REPO=\$1
    local MBOX=\$2
    local SUITESTART=\$3
    local RESULTS=\$4
TMPBUILD="\$(mktemp -d)"
BRANCH="\$(get-target-branch \$REPO \$MBOX | awk '{print \$NF}')"
cd \$REPO
source ./oe-init-build-env \$TMPBUILD
test-mboxes -r \$REPO -s \$SUITESTART -m \$MBOX -o \$RESULTS -- -b \$BRANCH
rm -rf \$TMPBUILD
 }
EOF

    # set path containing all patchtest and its scripts
    echo "export PATH=\"/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin:/home/patchtest/share/patchtest:/home/patchtest/share/patchtest/scripts:$PATH\"" >> ${IMAGE_ROOTFS}/home/patchtest/.bashrc

    # configure git, required for patchtest
    cat >> ${IMAGE_ROOTFS}/home/patchtest/.gitconfig << EOF
[user]
    name = patchtest
    email = patchtest@patchtest.com
EOF

}

IMAGE_PREPROCESS_COMMAND += "do_populate_patchtest_src; "
