# This can be merged into zamboni if we ever start building from
# the base image again
class zamboni_plus {

    file { "$PROJ_DIR/settings_local.py":
        ensure => file,
        source => "$PROJ_DIR/docs/settings/settings_local.dev.py",
        replace => false;
    }

    # Zamboni needs to allow for long-running commands.
    # This disables the SSH timeout.
    file { "/etc/ssh/ssh_config":
        ensure => file,
        source => "$PROJ_DIR/vagrant/files/etc/ssh/ssh_config",
        owner => "root",
        mode => 644,
        replace => true;
    }

    exec { "restart-ssh":
        command => "sudo /etc/init.d/ssh restart",
        require => File["/etc/ssh/ssh_config"]
    }

    package { ["screen", "subversion"]:
        ensure => installed
    }

    file { "/home/vagrant/.profile":
        source => "$PROJ_DIR/vagrant/files/home/vagrant/profile",
        owner => "vagrant",
        replace => true
    }
}
