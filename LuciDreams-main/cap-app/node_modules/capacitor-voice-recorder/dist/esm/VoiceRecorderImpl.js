import write_blob from 'capacitor-blob-writer';
import getBlobDuration from 'get-blob-duration';
import { RecordingStatus } from './definitions';
import { alreadyRecordingError, couldNotQueryPermissionStatusError, deviceCannotVoiceRecordError, emptyRecordingError, failedToFetchRecordingError, failedToRecordError, failureResponse, missingPermissionError, recordingHasNotStartedError, successResponse, } from './predefined-web-responses';
// these mime types will be checked one by one in order until one of them is found to be supported by the current browser
const POSSIBLE_MIME_TYPES = {
    'audio/aac': '.aac',
    'audio/mp4': '.mp3',
    'audio/webm;codecs=opus': '.ogg',
    'audio/webm': '.ogg',
    'audio/ogg;codecs=opus': '.ogg',
};
const neverResolvingPromise = () => new Promise(() => undefined);
export class VoiceRecorderImpl {
    constructor() {
        this.mediaRecorder = null;
        this.chunks = [];
        this.pendingResult = neverResolvingPromise();
    }
    static async canDeviceVoiceRecord() {
        var _a;
        if (((_a = navigator === null || navigator === void 0 ? void 0 : navigator.mediaDevices) === null || _a === void 0 ? void 0 : _a.getUserMedia) == null || VoiceRecorderImpl.getSupportedMimeType() == null) {
            return failureResponse();
        }
        else {
            return successResponse();
        }
    }
    async startRecording(options) {
        if (this.mediaRecorder != null) {
            throw alreadyRecordingError();
        }
        const deviceCanRecord = await VoiceRecorderImpl.canDeviceVoiceRecord();
        if (!deviceCanRecord.value) {
            throw deviceCannotVoiceRecordError();
        }
        const havingPermission = await VoiceRecorderImpl.hasAudioRecordingPermission().catch(() => successResponse());
        if (!havingPermission.value) {
            throw missingPermissionError();
        }
        return navigator.mediaDevices
            .getUserMedia({ audio: true })
            .then((stream) => this.onSuccessfullyStartedRecording(stream, options))
            .catch(this.onFailedToStartRecording.bind(this));
    }
    async stopRecording() {
        if (this.mediaRecorder == null) {
            throw recordingHasNotStartedError();
        }
        try {
            this.mediaRecorder.stop();
            this.mediaRecorder.stream.getTracks().forEach((track) => track.stop());
            return this.pendingResult;
        }
        catch (ignore) {
            throw failedToFetchRecordingError();
        }
        finally {
            this.prepareInstanceForNextOperation();
        }
    }
    static async hasAudioRecordingPermission() {
        if (navigator.permissions.query == null) {
            if (navigator.mediaDevices == null) {
                return Promise.reject(couldNotQueryPermissionStatusError());
            }
            return navigator.mediaDevices
                .getUserMedia({ audio: true })
                .then(() => successResponse())
                .catch(() => {
                throw couldNotQueryPermissionStatusError();
            });
        }
        return navigator.permissions
            .query({ name: 'microphone' })
            .then((result) => ({ value: result.state === 'granted' }))
            .catch(() => {
            throw couldNotQueryPermissionStatusError();
        });
    }
    static async requestAudioRecordingPermission() {
        const havingPermission = await VoiceRecorderImpl.hasAudioRecordingPermission().catch(() => failureResponse());
        if (havingPermission.value) {
            return successResponse();
        }
        return navigator.mediaDevices
            .getUserMedia({ audio: true })
            .then(() => successResponse())
            .catch(() => failureResponse());
    }
    pauseRecording() {
        if (this.mediaRecorder == null) {
            throw recordingHasNotStartedError();
        }
        else if (this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.pause();
            return Promise.resolve(successResponse());
        }
        else {
            return Promise.resolve(failureResponse());
        }
    }
    resumeRecording() {
        if (this.mediaRecorder == null) {
            throw recordingHasNotStartedError();
        }
        else if (this.mediaRecorder.state === 'paused') {
            this.mediaRecorder.resume();
            return Promise.resolve(successResponse());
        }
        else {
            return Promise.resolve(failureResponse());
        }
    }
    getCurrentStatus() {
        if (this.mediaRecorder == null) {
            return Promise.resolve({ status: RecordingStatus.NONE });
        }
        else if (this.mediaRecorder.state === 'recording') {
            return Promise.resolve({ status: RecordingStatus.RECORDING });
        }
        else if (this.mediaRecorder.state === 'paused') {
            return Promise.resolve({ status: RecordingStatus.PAUSED });
        }
        else {
            return Promise.resolve({ status: RecordingStatus.NONE });
        }
    }
    static getSupportedMimeType() {
        if ((MediaRecorder === null || MediaRecorder === void 0 ? void 0 : MediaRecorder.isTypeSupported) == null)
            return null;
        const foundSupportedType = Object.keys(POSSIBLE_MIME_TYPES).find((type) => MediaRecorder.isTypeSupported(type));
        return foundSupportedType !== null && foundSupportedType !== void 0 ? foundSupportedType : null;
    }
    onSuccessfullyStartedRecording(stream, options) {
        this.pendingResult = new Promise((resolve, reject) => {
            this.mediaRecorder = new MediaRecorder(stream);
            this.mediaRecorder.onerror = () => {
                this.prepareInstanceForNextOperation();
                reject(failedToRecordError());
            };
            this.mediaRecorder.onstop = async () => {
                var _a, _b, _c;
                const mimeType = VoiceRecorderImpl.getSupportedMimeType();
                if (mimeType == null) {
                    this.prepareInstanceForNextOperation();
                    reject(failedToFetchRecordingError());
                    return;
                }
                const blobVoiceRecording = new Blob(this.chunks, { type: mimeType });
                if (blobVoiceRecording.size <= 0) {
                    this.prepareInstanceForNextOperation();
                    reject(emptyRecordingError());
                    return;
                }
                let path;
                let recordDataBase64;
                if (options != null) {
                    const subDirectory = (_c = (_b = (_a = options.subDirectory) === null || _a === void 0 ? void 0 : _a.match(/^\/?(.+[^/])\/?$/)) === null || _b === void 0 ? void 0 : _b[1]) !== null && _c !== void 0 ? _c : '';
                    path = `${subDirectory}/recording-${new Date().getTime()}${POSSIBLE_MIME_TYPES[mimeType]}`;
                    await write_blob({
                        blob: blobVoiceRecording,
                        directory: options.directory,
                        fast_mode: true,
                        path,
                        recursive: true,
                    });
                }
                else {
                    recordDataBase64 = await VoiceRecorderImpl.blobToBase64(blobVoiceRecording);
                }
                const recordingDuration = await getBlobDuration(blobVoiceRecording);
                this.prepareInstanceForNextOperation();
                resolve({ value: { recordDataBase64, mimeType, msDuration: recordingDuration * 1000, path } });
            };
            this.mediaRecorder.ondataavailable = (event) => this.chunks.push(event.data);
            this.mediaRecorder.start();
        });
        return successResponse();
    }
    onFailedToStartRecording() {
        this.prepareInstanceForNextOperation();
        throw failedToRecordError();
    }
    static blobToBase64(blob) {
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => {
                const recordingResult = String(reader.result);
                const splitResult = recordingResult.split('base64,');
                const toResolve = splitResult.length > 1 ? splitResult[1] : recordingResult;
                resolve(toResolve.trim());
            };
            reader.readAsDataURL(blob);
        });
    }
    prepareInstanceForNextOperation() {
        if (this.mediaRecorder != null && this.mediaRecorder.state === 'recording') {
            try {
                this.mediaRecorder.stop();
            }
            catch (error) {
                console.warn('While trying to stop a media recorder, an error was thrown', error);
            }
        }
        this.pendingResult = neverResolvingPromise();
        this.mediaRecorder = null;
        this.chunks = [];
    }
}
//# sourceMappingURL=VoiceRecorderImpl.js.map