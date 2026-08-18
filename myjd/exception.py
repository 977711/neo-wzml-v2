# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from .const import (
    EXCEPTION_API_COMMAND_NOT_FOUND,
    EXCEPTION_API_INTERFACE_NOT_FOUND,
    EXCEPTION_AUTH_FAILED,
    EXCEPTION_BAD_PARAMETERS,
    EXCEPTION_BAD_REQUEST,
    EXCEPTION_CHALLENGE_FAILED,
    EXCEPTION_EMAIL_FORBIDDEN,
    EXCEPTION_EMAIL_INVALID,
    EXCEPTION_ERROR_EMAIL_NOT_CONFIRMED,
    EXCEPTION_FAILED,
    EXCEPTION_FILE_NOT_FOUND,
    EXCEPTION_INTERNAL_SERVER_ERROR,
    EXCEPTION_MAINTENANCE,
    EXCEPTION_METHOD_FORBIDDEN,
    EXCEPTION_OFFLINE,
    EXCEPTION_OUTDATED,
    EXCEPTION_OVERLOAD,
    EXCEPTION_SESSION,
    EXCEPTION_STORAGE_ALREADY_EXISTS,
    EXCEPTION_STORAGE_INVALID_KEY,
    EXCEPTION_STORAGE_INVALID_STORAGEID,
    EXCEPTION_STORAGE_KEY_NOT_FOUND,
    EXCEPTION_STORAGE_LIMIT_REACHED,
    EXCEPTION_STORAGE_NOT_FOUND,
    EXCEPTION_TOKEN_INVALID,
    EXCEPTION_TOO_MANY_REQUESTS,
    EXCEPTION_UNKNOWN,
)


class MYJDException(BaseException):
    pass


class MYJDConnectionException(MYJDException):
    pass


class MYJDDeviceNotFoundException(MYJDException):
    pass


class MYJDDecodeException(MYJDException):
    pass


class MYJDApiException(MYJDException):

    @classmethod
    def get_exception(
        cls, exception_source, exception_type=EXCEPTION_UNKNOWN, *args, **kwargs
    ):
        return EXCEPTION_CLASSES.get(exception_type.upper(), MYJDUnknownException)(
            exception_source, *args, **kwargs
        )

    def __init__(self, exception_source, *args, **kwargs):
        self.source = exception_source.upper()
        super(MYJDApiException, self).__init__(*args, **kwargs)


class MYJDApiCommandNotFoundException(MYJDApiException):
    pass


class MYJDApiInterfaceNotFoundException(MYJDApiException):
    pass


class MYJDAuthFailedException(MYJDApiException):
    pass


class MYJDBadParametersException(MYJDApiException):
    pass


class MYJDBadRequestException(MYJDApiException):
    pass


class MYJDChallengeFailedException(MYJDApiException):
    pass


class MYJDEmailForbiddenException(MYJDApiException):
    pass


class MYJDEmailInvalidException(MYJDApiException):
    pass


class MYJDErrorEmailNotConfirmedException(MYJDApiException):
    pass


class MYJDFailedException(MYJDApiException):
    pass


class MYJDFileNotFoundException(MYJDApiException):
    pass


class MYJDInternalServerErrorException(MYJDApiException):
    pass


class MYJDMaintenanceException(MYJDApiException):
    pass


class MYJDMethodForbiddenException(MYJDApiException):
    pass


class MYJDOfflineException(MYJDApiException):
    pass


class MYJDOutdatedException(MYJDApiException):
    pass


class MYJDOverloadException(MYJDApiException):
    pass


class MYJDSessionException(MYJDApiException):
    pass


class MYJDStorageAlreadyExistsException(MYJDApiException):
    pass


class MYJDStorageInvalidKeyException(MYJDApiException):
    pass


class MYJDStorageInvalidStorageIdException(MYJDApiException):
    pass


class MYJDStorageKeyNotFoundException(MYJDApiException):
    pass


class MYJDStorageLimitReachedException(MYJDApiException):
    pass


class MYJDStorageNotFoundException(MYJDApiException):
    pass


class MYJDTokenInvalidException(MYJDApiException):
    pass


class MYJDTooManyRequestsException(MYJDApiException):
    pass


class MYJDUnknownException(MYJDApiException):
    pass


EXCEPTION_CLASSES = {
    EXCEPTION_API_COMMAND_NOT_FOUND: MYJDApiCommandNotFoundException,
    EXCEPTION_API_INTERFACE_NOT_FOUND: MYJDApiInterfaceNotFoundException,
    EXCEPTION_AUTH_FAILED: MYJDAuthFailedException,
    EXCEPTION_BAD_PARAMETERS: MYJDBadParametersException,
    EXCEPTION_BAD_REQUEST: MYJDBadRequestException,
    EXCEPTION_CHALLENGE_FAILED: MYJDChallengeFailedException,
    EXCEPTION_EMAIL_FORBIDDEN: MYJDEmailForbiddenException,
    EXCEPTION_EMAIL_INVALID: MYJDEmailInvalidException,
    EXCEPTION_ERROR_EMAIL_NOT_CONFIRMED: MYJDErrorEmailNotConfirmedException,
    EXCEPTION_FAILED: MYJDFailedException,
    EXCEPTION_FILE_NOT_FOUND: MYJDFileNotFoundException,
    EXCEPTION_INTERNAL_SERVER_ERROR: MYJDInternalServerErrorException,
    EXCEPTION_MAINTENANCE: MYJDMaintenanceException,
    EXCEPTION_METHOD_FORBIDDEN: MYJDMethodForbiddenException,
    EXCEPTION_OFFLINE: MYJDOfflineException,
    EXCEPTION_OUTDATED: MYJDOutdatedException,
    EXCEPTION_OVERLOAD: MYJDOverloadException,
    EXCEPTION_SESSION: MYJDSessionException,
    EXCEPTION_STORAGE_ALREADY_EXISTS: MYJDStorageAlreadyExistsException,
    EXCEPTION_STORAGE_INVALID_KEY: MYJDStorageInvalidKeyException,
    EXCEPTION_STORAGE_INVALID_STORAGEID: MYJDStorageInvalidStorageIdException,
    EXCEPTION_STORAGE_KEY_NOT_FOUND: MYJDStorageKeyNotFoundException,
    EXCEPTION_STORAGE_LIMIT_REACHED: MYJDStorageLimitReachedException,
    EXCEPTION_STORAGE_NOT_FOUND: MYJDStorageNotFoundException,
    EXCEPTION_TOKEN_INVALID: MYJDTokenInvalidException,
    EXCEPTION_TOO_MANY_REQUESTS: MYJDTooManyRequestsException,
    EXCEPTION_UNKNOWN: MYJDUnknownException,
}
