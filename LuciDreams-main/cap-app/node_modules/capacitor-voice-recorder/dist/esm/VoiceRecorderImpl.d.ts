import type { CurrentRecordingStatus, GenericResponse, RecordingData, RecordingOptions } from './definitions';
declare const POSSIBLE_MIME_TYPES: {
    'audio/aac': string;
    'audio/mp4': string;
    'audio/webm;codecs=opus': string;
    'audio/webm': string;
    'audio/ogg;codecs=opus': string;
};
export declare class VoiceRecorderImpl {
    private mediaRecorder;
    private chunks;
    private pendingResult;
    static canDeviceVoiceRecord(): Promise<GenericResponse>;
    startRecording(options?: RecordingOptions): Promise<GenericResponse>;
    stopRecording(): Promise<RecordingData>;
    static hasAudioRecordingPermission(): Promise<GenericResponse>;
    static requestAudioRecordingPermission(): Promise<GenericResponse>;
    pauseRecording(): Promise<GenericResponse>;
    resumeRecording(): Promise<GenericResponse>;
    getCurrentStatus(): Promise<CurrentRecordingStatus>;
    static getSupportedMimeType<T extends keyof typeof POSSIBLE_MIME_TYPES>(): T | null;
    private onSuccessfullyStartedRecording;
    private onFailedToStartRecording;
    private static blobToBase64;
    private prepareInstanceForNextOperation;
}
export {};
