#!/usr/bin/env perl
# webTEX compile sandbox for COMPILE_BACKEND=local, based on Linux Landlock (kernel >= 5.13,
# unprivileged, works in a default Docker container). Since TeX Live 2026, kpathsea's openin_any
# is a no-op, and LuaTeX/biber never honoured it, so the kernel must keep a compile inside its
# project: the process and everything it starts can then only
#   - read, write and create files under the --rw directories (the project's compile directory),
#   - read and execute files under the --ro directories (system, TeX Live, the biber guard),
#   - read/write the --dev character devices (/dev/null, ...),
# and nothing else (no /data, no other projects, no /proc, no /tmp).
#
#   perl sandbox.pl --check                                  exit 0 if Landlock can be used
#   perl sandbox.pl --rw DIR [--ro DIR]... [--dev FILE]... -- COMMAND ARGS...
use strict;
use warnings;
use Config;

# Syscall numbers: Landlock's are the same on every architecture; prctl is not.
my ($SYS_CREATE, $SYS_ADD_RULE, $SYS_RESTRICT) = (444, 445, 446);
my %SYS_PRCTL = (x86_64 => 157, aarch64 => 167);
my $PR_SET_NO_NEW_PRIVS = 38;
my $O_PATH = 010000000;    # x86_64 and aarch64

sub fail { print STDERR "webTEX sandbox: $_[0]\n"; exit 126 }

my $abi = syscall($SYS_CREATE, 0, 0, 1);    # LANDLOCK_CREATE_RULESET_VERSION
if (@ARGV == 1 && $ARGV[0] eq '--check') {
  print "landlock abi $abi\n" if $abi > 0;
  exit($abi > 0 ? 0 : 1);
}
fail("Landlock is not available in this kernel ($!)") if $abi < 1;

my (@rw, @ro, @dev);
while (@ARGV && $ARGV[0] ne '--') {
  my $opt = shift @ARGV;
  my $val = shift @ARGV // fail("missing value for $opt");
  if    ($opt eq '--rw')  { push @rw, $val }
  elsif ($opt eq '--ro')  { push @ro, $val }
  elsif ($opt eq '--dev') { push @dev, $val }
  else                    { fail("unknown option $opt") }
}
shift @ARGV;
fail('no command') unless @ARGV;
fail('no --rw directory') unless @rw;

# Access rights (LANDLOCK_ACCESS_FS_*): ABI 1 has bits 0..12, ABI 2 adds REFER (13), ABI 3 TRUNCATE (14).
my $all = $abi >= 3 ? (1 << 15) - 1 : $abi == 2 ? (1 << 14) - 1 : (1 << 13) - 1;
my ($EXECUTE, $WRITE_FILE, $READ_FILE, $READ_DIR) = (1, 2, 4, 8);
my $TRUNCATE = $abi >= 3 ? 1 << 14 : 0;
my $ro_dir = $EXECUTE | $READ_FILE | $READ_DIR;
my $dev_file = $READ_FILE | $WRITE_FILE | $TRUNCATE;

my $ruleset = syscall($SYS_CREATE, pack('Q', $all), 8, 0);
fail("landlock_create_ruleset: $!") if $ruleset < 0;

sub allow {
  my ($path, $access, $required) = @_;
  my $fh;
  unless (sysopen($fh, $path, $O_PATH)) {
    fail("cannot open $path: $!") if $required;
    return;    # optional system paths (e.g. /lib64) may not exist
  }
  # struct landlock_path_beneath_attr { __u64 allowed_access; __s32 parent_fd; } (packed)
  my $attr = pack('Ql', $access, fileno($fh));
  syscall($SYS_ADD_RULE, $ruleset, 1, $attr, 0) == 0 or fail("landlock_add_rule $path: $!");
  close $fh;
}
allow($_, $all, 1) for @rw;
allow($_, $ro_dir, 0) for @ro;
allow($_, $dev_file, 0) for @dev;

my $prctl = $SYS_PRCTL{ (split /-/, $Config{archname})[0] } // fail("unsupported architecture $Config{archname}");
syscall($prctl, $PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0 or fail("prctl(NO_NEW_PRIVS): $!");
syscall($SYS_RESTRICT, $ruleset, 0) == 0 or fail("landlock_restrict_self: $!");
exec { $ARGV[0] } @ARGV or fail("cannot run $ARGV[0]: $!");
