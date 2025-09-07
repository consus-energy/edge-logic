LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "default": {
            "format": "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S"
        }
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": "DEBUG"
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": "logs/edge.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf8",
            "level": "DEBUG"
        },
        "modbus_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": "logs/modbus.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf8",
            "level": "DEBUG"
        }
    },

    "root": {
        "handlers": ["console", "file"],
        "level": "DEBUG"
    },

    "loggers": {
        "pymodbus": {
            "handlers": ["modbus_file"],
            "level": "DEBUG",
            "propagate": False
        },
        "modbus": {
            "handlers": ["modbus_file"],
            "level": "DEBUG",
            "propagate": False
        }
    }
}
